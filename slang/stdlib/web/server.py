"""slang 标准库的 Flask 风格 Web 运行时。

设计目标：
  * 不依赖第三方包，直接使用 Python 标准库 http.server；
  * 对 slang 暴露扁平函数，避免 slang 缺少类/装饰器带来的语法负担；
  * 同时提供 ``handle``/``start`` 便于测试和嵌入，不必每次起阻塞服务器。

slang 侧的入口在 ``slang/stdlib/web/flask.lang``。
"""
import html
import json
import mimetypes
import os
import re
import secrets
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

_SESSION_COOKIE = "slang_session"
_DEFAULT_MAX_BODY = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------
class Request:
    def __init__(self, app, method, path, query_string="", headers=None,
                 body=b"", params=None):
        self.app = app
        self.method = method.upper()
        self.path = path
        self.query_string = query_string or ""
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.params = dict(params or {})
        self._query = None
        self._cookies = None
        self._form = None
        self._json = _UNSET

    def query(self, name, default=""):
        if self._query is None:
            self._query = parse_qs(self.query_string, keep_blank_values=True)
        values = self._query.get(name)
        return values[0] if values else default

    def param(self, name, default=""):
        return self.params.get(name, default)

    def header(self, name, default=""):
        return self.headers.get(str(name).lower(), default)

    def cookie(self, name, default=""):
        if self._cookies is None:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("cookie", ""))
            except Exception:
                jar = SimpleCookie()
            self._cookies = {key: morsel.value for key, morsel in jar.items()}
        return self._cookies.get(name, default)

    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        if self._json is _UNSET:
            try:
                self._json = json.loads(self.body.decode("utf-8")) if self.body else None
            except (ValueError, UnicodeDecodeError):
                self._json = None
        return self._json

    def form(self, name, default=""):
        if self._form is None:
            ctype = self.header("content-type", "")
            if "application/x-www-form-urlencoded" in ctype:
                self._form = parse_qs(self.body.decode("utf-8", "replace"),
                                      keep_blank_values=True)
            else:
                self._form = {}
        values = self._form.get(name)
        return values[0] if values else default

    @property
    def session_id(self):
        return self.cookie(_SESSION_COOKIE, "")


class Response:
    def __init__(self, body=b"", status=200, headers=None,
                 content_type="text/html; charset=utf-8"):
        self.status = int(status)
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif not isinstance(body, (bytes, bytearray)):
            body = str(body).encode("utf-8")
        self.body = bytes(body)
        self.headers = {}
        if content_type:
            self.headers["Content-Type"] = content_type
        for name, value in (headers or {}).items():
            self.headers[str(name)] = str(value)
        self.cookies = []

    def set_header(self, name, value):
        self.headers[str(name)] = str(value)

    def get_header(self, name, default=""):
        target = str(name).lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return default

    def set_cookie(self, name, value, max_age=None, path="/",
                   httponly=True, samesite="Lax"):
        cookie = SimpleCookie()
        cookie[name] = str(value)
        morsel = cookie[name]
        morsel["path"] = path or "/"
        if max_age is not None and int(max_age) >= 0:
            morsel["max-age"] = str(int(max_age))
        if httponly:
            morsel["httponly"] = True
        if samesite:
            morsel["samesite"] = samesite
        self.cookies.append(morsel.OutputString())


_UNSET = object()


class _Route:
    def __init__(self, methods, pattern, handler):
        self.methods = _methods(methods)
        self.pattern = pattern
        self.handler = handler
        self.regex, self.converters = _compile_pattern(pattern)

    def match(self, path):
        found = self.regex.match(path)
        if not found:
            return None
        params = {}
        for name, value in found.groupdict().items():
            converter = self.converters.get(name, "str")
            if converter == "int":
                try:
                    value = int(value)
                except ValueError:
                    return None
            elif converter == "float":
                try:
                    value = float(value)
                except ValueError:
                    return None
            params[name] = value
        return params


class Group:
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = "/" + str(prefix or "").strip("/")


class Application:
    def __init__(self, name, template_folder="templates", static_folder="static"):
        self.name = name
        self.template_folder = os.path.abspath(template_folder or "templates")
        self.static_folder = os.path.abspath(static_folder or "static")
        self.routes = []
        self.before = []
        self.after = []
        self.errors = {}
        self.statics = []
        self.cors_headers = {}
        self.debug = False
        self.sessions = {}
        self.session_lock = threading.Lock()

    def add_route(self, methods, pattern, handler):
        self.routes.append(_Route(methods, pattern, handler))

    def add_static(self, prefix, directory):
        prefix = "/" + str(prefix or "").strip("/")
        self.statics.append((prefix, os.path.abspath(directory)))
        # 长前缀优先，避免 /static 抢走 /static/private。
        self.statics.sort(key=lambda item: len(item[0]), reverse=True)

    def add_before(self, handler):
        self.before.append(handler)

    def add_after(self, handler):
        self.after.append(handler)

    def add_error(self, code, handler):
        self.errors[int(code)] = handler

    def match(self, method, path):
        method = method.upper()
        path_match = None
        for route in self.routes:
            params = route.match(path)
            if params is None:
                continue
            if path_match is None:
                path_match = route
            wanted = "GET" if method == "HEAD" else method
            if wanted in route.methods or "ANY" in route.methods:
                return route, params, None
        return None, None, path_match

    def dispatch(self, method, path, query_string="", headers=None, body=b""):
        method = str(method).upper()
        path = unquote(path or "/")
        if not path.startswith("/"):
            path = "/" + path
        req = Request(self, method, path, query_string, headers, body)

        # 静态文件优先；不执行 before/after 钩子。
        static = self._static_response(path)
        if static is not None:
            return self._finish(req, static)

        if method == "OPTIONS":
            response = Response("", 204, content_type="")
            if self.cors_headers:
                for name, value in self.cors_headers.items():
                    response.set_header(name, value)
            return response

        route, params, path_match = self.match(method, path)
        if route is None:
            if path_match is not None:
                response = self._error(req, 405)
                allow = sorted(path_match.methods - {"ANY"})
                if allow:
                    response.set_header("Allow", ", ".join(allow))
            else:
                response = self._error(req, 404)
            return self._finish(req, response)

        req.params = params or {}
        response = None
        for hook in self.before:
            result = hook(req)
            if _hook_result(result):
                response = _normalize(result)
                break
        if response is None:
            try:
                response = _normalize(route.handler(req))
            except Exception as exc:
                response = self._exception_response(req, exc)

        for hook in self.after:
            try:
                result = hook(req, response)
                if _hook_result(result):
                    response = _normalize(result)
            except Exception as exc:
                response = self._exception_response(req, exc)
                break

        # 让 session_set 等辅助函数知道当前请求。
        response.request = req
        return self._finish(req, response)

    def _finish(self, req, response):
        response.request = req
        for name, value in self.cors_headers.items():
            response.set_header(name, value)
        if "Content-Length" not in response.headers:
            response.headers["Content-Length"] = str(len(response.body))
        return response

    def _static_response(self, path):
        for prefix, directory in self.statics:
            if path != prefix and not path.startswith(prefix + "/"):
                continue
            rel = path[len(prefix):].lstrip("/")
            if not rel:
                rel = "index.html"
            target = os.path.realpath(os.path.join(directory, rel))
            root = os.path.realpath(directory)
            if target != root and not target.startswith(root + os.sep):
                return Response("Forbidden", 403, content_type="text/plain; charset=utf-8")
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if not os.path.isfile(target):
                continue
            return send_file(target)
        return None

    def _error(self, req, code):
        handler = self.errors.get(code)
        if handler is not None:
            try:
                return _normalize(handler(req))
            except Exception as exc:
                return self._exception_response(req, exc)
        messages = {
            404: "404 Not Found",
            405: "405 Method Not Allowed",
            413: "413 Payload Too Large",
        }
        return Response(messages.get(code, "%d Error" % code), code,
                        content_type="text/plain; charset=utf-8")

    def _exception_response(self, req, exc):
        if self.debug:
            detail = traceback.format_exc()
            return Response("500 Internal Server Error\n\n" + detail, 500,
                            content_type="text/plain; charset=utf-8")
        handler = self.errors.get(500)
        if handler is not None:
            try:
                return _normalize(handler(req))
            except Exception:
                pass
        return Response("500 Internal Server Error", 500,
                        content_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# 路由 / 工具
# ---------------------------------------------------------------------------
def _methods(value):
    if isinstance(value, str):
        parts = re.split(r"[, ]+", value.strip())
        return {part.upper() for part in parts if part}
    return {str(part).upper() for part in value}


def _compile_pattern(pattern):
    pattern = "/" + str(pattern or "").strip("/")
    regex = "^"
    converters = {}
    pos = 0
    tag = re.compile(r"<(?:(int|float|path|str):)?([A-Za-z_][A-Za-z0-9_]*)>")
    for found in tag.finditer(pattern):
        regex += re.escape(pattern[pos:found.start()])
        converter = found.group(1) or "str"
        name = found.group(2)
        converters[name] = converter
        if converter == "int":
            regex += r"(?P<%s>-?\d+)" % name
        elif converter == "float":
            regex += r"(?P<%s>-?\d+(?:\.\d+)?)" % name
        elif converter == "path":
            regex += r"(?P<%s>.+)" % name
        else:
            regex += r"(?P<%s>[^/]+)" % name
        pos = found.end()
    regex += re.escape(pattern[pos:]) + "$"
    return re.compile(regex), converters


def _hook_result(value):
    # slang 的 nil 是空列表；before/after 返回 nil 表示“继续”。
    return value is not None and value != []


def _normalize(value):
    if isinstance(value, Response):
        return value
    if value is None:
        return Response("", 200, content_type="text/plain; charset=utf-8")
    if isinstance(value, bytes):
        return Response(value, 200, content_type="application/octet-stream")
    if isinstance(value, str):
        return text(value)
    if isinstance(value, (dict, list, tuple)):
        return json_response(value)
    return text(str(value))


def _pairs_to_dict(pairs):
    if pairs is None:
        return {}
    if isinstance(pairs, dict):
        return dict(pairs)
    result = {}
    for index in range(0, len(pairs) - 1, 2):
        result[pairs[index]] = pairs[index + 1]
    return result


# ---------------------------------------------------------------------------
# 应用 / 路由注册
# ---------------------------------------------------------------------------
def new_app(name, template_folder="templates", static_folder="static"):
    return Application(name, template_folder, static_folder)


def _target(target):
    if isinstance(target, Group):
        return target.app, target.prefix
    return target, ""


def _join(prefix, pattern):
    pattern = "/" + str(pattern or "").strip("/")
    if not prefix:
        return pattern
    if pattern == "/":
        return prefix or "/"
    return prefix + pattern


def add_route(target, methods, pattern, handler):
    app, prefix = _target(target)
    app.add_route(methods, _join(prefix, pattern), handler)
    return app


def add_before(target, handler):
    app, prefix = _target(target)
    if prefix:
        def grouped(req):
            if req.path == prefix or req.path.startswith(prefix + "/"):
                return handler(req)
            return None
        app.add_before(grouped)
    else:
        app.add_before(handler)
    return app


def add_after(target, handler):
    app, prefix = _target(target)
    if prefix:
        def grouped(req, response):
            if req.path == prefix or req.path.startswith(prefix + "/"):
                return handler(req, response)
            return None
        app.add_after(grouped)
    else:
        app.add_after(handler)
    return app


def add_error(target, code, handler):
    app, _ = _target(target)
    app.add_error(code, handler)
    return app


def add_static(target, prefix, directory):
    app, base = _target(target)
    app.add_static(_join(base, prefix), directory)
    return app


def group(target, prefix):
    app, base = _target(target)
    return Group(app, _join(base, prefix))


def cors(target, origin="*", methods="GET, POST, PUT, DELETE, PATCH, OPTIONS",
         headers="Content-Type, Authorization"):
    app, _ = _target(target)
    app.cors_headers = {
        "Access-Control-Allow-Origin": str(origin),
        "Access-Control-Allow-Methods": str(methods),
        "Access-Control-Allow-Headers": str(headers),
    }
    return app


def set_debug(target, enabled=True):
    app, _ = _target(target)
    app.debug = bool(enabled)
    return app


# ---------------------------------------------------------------------------
# 响应构造
# ---------------------------------------------------------------------------
def text(body, status=200):
    return Response(body, status, content_type="text/plain; charset=utf-8")


def html_response(body, status=200):
    return Response(body, status, content_type="text/html; charset=utf-8")


def json_response(value, status=200):
    body = json.dumps(value, ensure_ascii=False, default=str)
    return Response(body, status, content_type="application/json; charset=utf-8")


def redirect(location, status=302):
    response = Response("", status, content_type="text/plain; charset=utf-8")
    response.set_header("Location", str(location))
    return response


def send_file(path, content_type=None):
    path = os.path.abspath(str(path))
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except OSError:
        return Response("404 Not Found", 404, content_type="text/plain; charset=utf-8")
    if content_type is None:
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(body, 200, content_type=content_type)


def set_status(response, status):
    response.status = int(status)
    return response


def set_header(response, name, value):
    response.set_header(name, value)
    return response


def set_cookie(response, name, value, max_age=-1):
    response.set_cookie(name, value, max_age=None if int(max_age) < 0 else int(max_age))
    return response


# ---------------------------------------------------------------------------
# 请求便捷函数
# ---------------------------------------------------------------------------
def request_query(req, name, default=""):
    return req.query(name, default)


def request_param(req, name, default=""):
    return req.param(name, default)


def request_header(req, name, default=""):
    return req.header(name, default)


def request_cookie(req, name, default=""):
    return req.cookie(name, default)


def request_json(req):
    return req.json()


def request_form(req, name, default=""):
    return req.form(name, default)


# ---------------------------------------------------------------------------
# Session（服务器端存储 + cookie id）
# ---------------------------------------------------------------------------
def _session(req, create=False):
    app = req.app
    sid = req.session_id
    with app.session_lock:
        data = app.sessions.get(sid) if sid else None
        if data is None and create:
            sid = secrets.token_urlsafe(24)
            data = {}
            app.sessions[sid] = data
            req._cookies = dict(req._cookies or {})
            req._cookies[_SESSION_COOKIE] = sid
    return sid, data


def session_get(req, key, default=""):
    _, data = _session(req, create=False)
    return default if data is None else data.get(key, default)


def session_set(req, response, key, value):
    sid, data = _session(req, create=True)
    data[key] = value
    response.set_cookie(_SESSION_COOKIE, sid, httponly=True, samesite="Lax")
    return response


def session_clear(req, response):
    app = req.app
    sid = req.session_id
    if sid:
        with app.session_lock:
            app.sessions.pop(sid, None)
    response.set_cookie(_SESSION_COOKIE, "", max_age=0)
    return response


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"({%.*?%}|{{.*?}})", re.S)
_FOR_RE = re.compile(r"for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+([A-Za-z0-9_.]+)")
_IF_RE = re.compile(r"if\s+(.+)")


def _lookup(context, path):
    value = context
    for part in str(path).strip().split("."):
        if not part:
            continue
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            value = getattr(value, part, "")
        if value == "":
            return ""
    return value


def _truthy(value):
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def _eval_var(expr, context):
    parts = [part.strip() for part in expr.split("|")]
    value = _lookup(context, parts[0])
    safe = False
    for filt in parts[1:]:
        if filt == "safe":
            safe = True
        elif filt == "upper":
            value = str(value).upper()
        elif filt == "lower":
            value = str(value).lower()
        elif filt == "length":
            try:
                value = len(value)
            except TypeError:
                value = 0
        elif filt.startswith("default:"):
            if value == "":
                value = filt[len("default:"):].strip().strip("'\"")
    if safe:
        return "" if value is None else str(value)
    return html.escape("" if value is None else str(value))


def _find_end(tokens, start, open_name, close_name):
    depth = 1
    for index in range(start, len(tokens)):
        token = tokens[index]
        if not token.startswith("{%"):
            continue
        stmt = token[2:-2].strip()
        if stmt.split(" ", 1)[0] == open_name:
            depth += 1
        elif stmt == close_name:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _find_else(tokens, start, end):
    depth = 1
    for index in range(start, end):
        token = tokens[index]
        if not token.startswith("{%"):
            continue
        stmt = token[2:-2].strip()
        if stmt.startswith("if "):
            depth += 1
        elif stmt == "endif":
            depth -= 1
        elif stmt == "else" and depth == 1:
            return index
    return -1


def _render_tokens(tokens, context):
    out = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("{%"):
            stmt = token[2:-2].strip()
            if stmt.startswith("for "):
                found = _FOR_RE.match(stmt)
                end = _find_end(tokens, index + 1, "for", "endfor")
                if not found or end < 0:
                    raise ValueError("模板 for 块不完整")
                var, path = found.group(1), found.group(2)
                values = _lookup(context, path)
                if not isinstance(values, (list, tuple)):
                    values = []
                body = tokens[index + 1:end]
                for item in values:
                    child = dict(context)
                    child[var] = item
                    out.append(_render_tokens(body, child))
                index = end + 1
                continue
            if stmt.startswith("if "):
                found = _IF_RE.match(stmt)
                end = _find_end(tokens, index + 1, "if", "endif")
                if not found or end < 0:
                    raise ValueError("模板 if 块不完整")
                else_index = _find_else(tokens, index + 1, end)
                cond = _truthy(_lookup(context, found.group(1).strip()))
                if else_index >= 0:
                    chosen = tokens[index + 1:else_index] if cond else tokens[else_index + 1:end]
                else:
                    chosen = tokens[index + 1:end] if cond else []
                out.append(_render_tokens(chosen, context))
                index = end + 1
                continue
            # 其他标签忽略
            index += 1
            continue
        if token.startswith("{{"):
            out.append(_eval_var(token[2:-2], context))
        else:
            out.append(token)
        index += 1
    return "".join(out)


def render(target, name, context=None):
    app, _ = _target(target)
    path = str(name)
    if not os.path.isabs(path):
        path = os.path.join(app.template_folder, path)
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tokens = _TAG_RE.split(source)
    return html_response(_render_tokens(tokens, _pairs_to_dict(context)))


def render_kv(target, name, pairs):
    return render(target, name, _pairs_to_dict(pairs))


# ---------------------------------------------------------------------------
# 测试 / 服务器
# ---------------------------------------------------------------------------
def handle(target, method, path, body="", headers=None):
    app, prefix = _target(target)
    full = _join(prefix, path)
    parsed = urlsplit(full)
    return app.dispatch(method, parsed.path, parsed.query, headers or {}, body)


class Server:
    def __init__(self, httpd, thread, host, port):
        self.httpd = httpd
        self.thread = thread
        self.host = host
        self.port = port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        return True

    def url(self, path="/"):
        return "http://%s:%d%s" % (self.host, self.port, path)


def _make_handler(application):
    class Handler(BaseHTTPRequestHandler):
        app = application
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if self.app.debug:
                BaseHTTPRequestHandler.log_message(self, fmt, *args)

        def _read_body(self):
            length = self.headers.get("Content-Length")
            if not length:
                return b""
            try:
                size = int(length)
            except ValueError:
                return b""
            if size < 0 or size > _DEFAULT_MAX_BODY:
                return None
            return self.rfile.read(size)

        def _handle(self):
            body = self._read_body()
            if body is None:
                response = Response("413 Payload Too Large", 413,
                                    content_type="text/plain; charset=utf-8")
            else:
                parsed = urlsplit(self.path)
                headers = {key: value for key, value in self.headers.items()}
                response = self.app.dispatch(self.command, parsed.path,
                                             parsed.query, headers, body)
            self.send_response(response.status)
            for name, value in response.headers.items():
                if name.lower() == "content-length":
                    continue
                self.send_header(name, value)
            for cookie in response.cookies:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if self.command != "HEAD" and response.body:
                self.wfile.write(response.body)

        do_GET = _handle
        do_HEAD = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_OPTIONS = _handle

    return Handler


def run(target, host="127.0.0.1", port=5000):
    app, _ = _target(target)
    httpd = ThreadingHTTPServer((str(host), int(port)), _make_handler(app))
    actual_host, actual_port = httpd.server_address[:2]
    print(" * slang web running on http://%s:%s" % (actual_host, actual_port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return True


def start(target, host="127.0.0.1", port=5000):
    app, _ = _target(target)
    httpd = ThreadingHTTPServer((str(host), int(port)), _make_handler(app))
    actual_host, actual_port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return Server(httpd, thread, actual_host, actual_port)


def stop(server):
    return server.stop()


def url(server, path="/"):
    return server.url(path)


def response_status(response):
    return response.status


def response_text(response):
    return response.body.decode("utf-8", "replace")


def response_header(response, name, default=""):
    return response.get_header(name, default)


def response_json(response):
    try:
        return json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def response_cookies(response):
    return list(response.cookies)
