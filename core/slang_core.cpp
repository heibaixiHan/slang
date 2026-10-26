// slang_core.cpp —— slang 的 C++ 核心（词法分析器）
//
// 设计目标：性能敏感、变化少的底层部分放在这里，编译一次长期使用；
// 复杂/易变的部分（解析、求值、加载逻辑）留在 Python 侧。
//
// Python 通过 ctypes 调用（ABI 2）：
//   int   slang_abi_version(void)  -> 必须与 Python 侧常量一致
//   char* slang_lex(const char* src, size_t len, int mode, size_t* out_len)
//   mode: 0 = sexpr, 1 = auto；src 允许包含 NUL，长度由 len 显式给出
//   返回 malloc 的 JSON token 数组： [["LP","(",line,col,end], ...]
//   数字 token 使用原文（只做 JSON 合法性归一化），避免精度/范围丢失
//   出错返回 NULL，可调用 slang_last_error() 取错误信息。
//
// 行为必须与 slang/lexer.py 的纯 Python 实现保持一致（tests 会对比两者）。

#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

static std::string g_error;

static const char* OP_CHARS = "+-*/<>=!";
static const char* WHITESPACE = " \t\r";

static bool is_op_char(char c) {
    return c != '\0' && strchr(OP_CHARS, c) != nullptr;
}
static bool is_op_char_mode(char c, int mode) {
    return is_op_char(c) || (mode == 1 && c == '%');
}
static bool is_ws(char c) {
    return c != '\0' && strchr(WHITESPACE, c) != nullptr;
}
static bool is_atom_end(char c) {
    if (c == '\0') return false;  // NUL 是普通字符，与 Python 词法器一致
    if (is_ws(c) || c == '\n') return true;
    return strchr("()[]{};,#\"", c) != nullptr;
}

namespace {

void json_escape(const std::string& s, std::string& out) {
    char buf[8];
    for (size_t i = 0; i < s.size(); ++i) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
}

// 是否 UTF-8 连续字节（用于把列号按“字符”而不是“字节”计算，和 Python 一致）
bool is_continuation(unsigned char c) { return (c & 0xC0) == 0x80; }

// 把 Python 数字字面量原文改写成合法 JSON 数字，且数值保持不变：
//   +1 -> 1      .5 -> 0.5      -.5 -> -0.5      1. -> 1.0      1.e3 -> 1.0e3
//   00 -> 0      007 -> 7       -00.5 -> -0.5    （JSON 不允许前导零）
std::string normalize_num_text(const std::string& text) {
    std::string t = text;
    if (!t.empty() && t[0] == '+') t.erase(0, 1);

    size_t sign = 0;
    if (sign < t.size() && t[sign] == '-') sign++;
    size_t int_start = sign;
    size_t int_end = int_start;
    while (int_end < t.size() && std::isdigit((unsigned char)t[int_end])) int_end++;
    if (int_end > int_start) {
        size_t nz = int_start;
        while (nz + 1 < int_end && t[nz] == '0') nz++;
        if (nz > int_start) t.erase(int_start, nz - int_start);
    }

    size_t p = sign;
    if (p < t.size() && t[p] == '.') t.insert(p, "0");
    size_t dot = t.find('.');
    if (dot != std::string::npos &&
        (dot + 1 >= t.size() || t[dot + 1] == 'e' || t[dot + 1] == 'E')) {
        t.insert(dot + 1, "0");
    }
    return t;
}

// 尝试在位置 i 匹配数字字面量，返回匹配长度（0 表示不是数字）。
// sexpr 模式允许前导 +/-；auto 模式把它留给 parser 当一元/二元运算符，
// 从而与 Python 词法器一致（`1 +2` 不再被吞成两个数字）。
size_t match_number(const std::string& s, size_t i, bool allow_sign) {
    size_t j = i;
    if (allow_sign && j < s.size() && (s[j] == '+' || s[j] == '-')) j++;
    size_t digits = 0;
    while (j < s.size() && isdigit((unsigned char)s[j])) {
        j++;
        digits++;
    }
    if (digits == 0) {
        // .\d+ 形式
        if (j < s.size() && s[j] == '.') {
            size_t k = j + 1;
            size_t frac = 0;
            while (k < s.size() && isdigit((unsigned char)s[k])) {
                k++;
                frac++;
            }
            if (frac == 0) return 0;
            j = k;
        } else {
            return 0;
        }
    } else if (j < s.size() && s[j] == '.') {
        j++;
        while (j < s.size() && isdigit((unsigned char)s[j])) j++;
    }
    if (j < s.size() && (s[j] == 'e' || s[j] == 'E')) {
        size_t k = j + 1;
        if (k < s.size() && (s[k] == '+' || s[k] == '-')) k++;
        size_t exp_digits = 0;
        while (k < s.size() && isdigit((unsigned char)s[k])) {
            k++;
            exp_digits++;
        }
        if (exp_digits > 0) j = k;
    }
    return j - i;
}

void append_utf8(unsigned int cp, std::string& out) {
    if (cp <= 0x7F) {
        out += static_cast<char>(cp);
    } else if (cp <= 0x7FF) {
        out += static_cast<char>(0xC0 | (cp >> 6));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    } else {
        out += static_cast<char>(0xE0 | (cp >> 12));
        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    }
}

int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool unescape(const std::string& raw, std::string& out, std::string& err) {
    for (size_t i = 0; i < raw.size(); ++i) {
        if (raw[i] == '\\' && i + 1 < raw.size()) {
            char e = raw[++i];
            switch (e) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case '\\': out += '\\'; break;
                case '"': out += '"'; break;
                case '0': out += '\0'; break;
                case 'x':
                case 'u': {
                    size_t width = e == 'x' ? 2 : 4;
                    if (i + width >= raw.size()) {
                        err = e == 'x' ? "\\x 转义需要两位十六进制数"
                                       : "\\u 转义需要四位十六进制数";
                        return false;
                    }
                    unsigned int cp = 0;
                    for (size_t k = 1; k <= width; ++k) {
                        int hv = hex_value(raw[i + k]);
                        if (hv < 0) {
                            err = e == 'x' ? "\\x 转义需要两位十六进制数"
                                           : "\\u 转义需要四位十六进制数";
                            return false;
                        }
                        cp = (cp << 4) | static_cast<unsigned int>(hv);
                    }
                    append_utf8(cp, out);
                    i += width;
                    break;
                }
                default: out += '\\'; out += e;
            }
        } else {
            out += raw[i];
        }
    }
    return true;
}

// 与 Python read_source/lexer 一致：CRLF 和裸 CR 都归一化为 LF。
std::string normalize_newlines(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\r') {
            out += '\n';
            if (i + 1 < s.size() && s[i + 1] == '\n') i++;
        } else {
            out += s[i];
        }
    }
    return out;
}

struct Lexer {
    const std::string& src;
    int mode;
    std::vector<int> cp;  // 每个字节位置的“字符序号”，用于列号
    std::string out;
    bool first = true;

    Lexer(const std::string& s, int m) : src(s), mode(m) {
        cp.resize(s.size() + 1);
        cp[0] = 0;
        for (size_t i = 0; i < s.size(); ++i) {
            cp[i + 1] = cp[i] + (is_continuation((unsigned char)s[i]) ? 0 : 1);
        }
    }

    int col(size_t i, size_t line_start) const {
        return cp[i] - cp[line_start] + 1;
    }

    void emit(const char* kind, const std::string& value, size_t line, size_t colv,
              size_t end, bool has_value) {
        if (!first) out += ",";
        first = false;
        out += "[\"";
        out += kind;
        out += "\"";
        if (has_value) {
            out += ",";
            if (strcmp(kind, "NUM") == 0) {
                out += value;
            } else {
                out += "\"";
                json_escape(value, out);
                out += "\"";
            }
        }
        char buf[64];
        snprintf(buf, sizeof(buf), ",%zu,%zu,%zu]", line, colv, end);
        out += buf;
    }

    bool fail(const std::string& msg, size_t line, size_t colv) {
        char buf[64];
        snprintf(buf, sizeof(buf), "第 %zu 行 第 %zu 列: ", line, colv);
        g_error = std::string(buf) + msg;
        return false;
    }

    bool run() {
        size_t n = src.size();
        size_t i = 0;
        size_t line = 1;
        size_t line_start = 0;

        while (i < n) {
            char ch = src[i];
            if (ch == '\n') {
                line++;
                i++;
                line_start = i;
                continue;
            }
            if (is_ws(ch)) {
                i++;
                continue;
            }
            if (ch == ';' || ch == '#') {
                while (i < n && src[i] != '\n') i++;
                continue;
            }
            size_t c = col(i, line_start);

            if (ch == ',') {
                // 两种模式都产生逗号：@sexpr 由 parser 报错，@auto 作为分隔符
                emit("COMMA", ",", line, c, c + 1, true);
                i++;
                continue;
            }

            if (ch == '(' || ch == '[' || ch == '{') {
                const char* kind = ch == '(' ? "LP" : (ch == '[' ? "LB" : "LC");
                emit(kind, std::string(1, ch), line, c, c + 1, true);
                i++;
                continue;
            }
            if (ch == ')' || ch == ']' || ch == '}') {
                const char* kind = ch == ')' ? "RP" : (ch == ']' ? "RB" : "RC");
                emit(kind, std::string(1, ch), line, c, c + 1, true);
                i++;
                continue;
            }

            if (ch == '"') {
                size_t j = i + 1;
                std::string raw;
                while (j < n && src[j] != '"') {
                    if (src[j] == '\\' && j + 1 < n) {
                        if (src[j + 1] == '\n')
                            return fail("字符串不能跨行（不支持反斜杠续行）", line, c);
                        raw += src[j];
                        raw += src[j + 1];
                        j += 2;
                    } else if (src[j] == '\n') {
                        return fail("字符串不能跨行（请用 \\n 转义）", line, c);
                    } else {
                        raw += src[j];
                        j++;
                    }
                }
                if (j >= n) return fail("字符串未闭合", line, c);
                std::string decoded;
                std::string escape_err;
                if (!unescape(raw, decoded, escape_err)) {
                    return fail(escape_err, line, c);
                }
                emit("STR", decoded, line, c, col(j + 1, line_start), true);
                i = j + 1;
                continue;
            }

            size_t num_len = match_number(src, i, mode == 0);
            if (num_len > 0 && (i + num_len >= n || is_atom_end(src[i + num_len]))) {
                // 原文交给 json.loads 解析：大整数保持任意精度，1e400 变成 inf，
                // 与 Python 词法器 int()/float() 的结果一致。
                std::string text = normalize_num_text(src.substr(i, num_len));
                emit("NUM", text, line, c, col(i + num_len, line_start), true);
                i += num_len;
                continue;
            }

            if (is_op_char_mode(ch, mode)) {
                size_t j = i;
                while (j < n && is_op_char_mode(src[j], mode)) j++;
                emit("OP", src.substr(i, j - i), line, c, col(j, line_start), true);
                i = j;
                continue;
            }

            size_t j = i;
            while (j < n && !is_atom_end(src[j])) j++;
            if (j == i) {
                char buf[64];
                snprintf(buf, sizeof(buf), "无法识别的字符 '%c'", ch);
                return fail(buf, line, c);
            }
            emit("SYM", src.substr(i, j - i), line, c, col(j, line_start), true);
            i = j;
        }

        for (size_t k = 0; k <= n; ++k) {
            (void)cp[k];
        }
        return true;
    }
};

}  // namespace

extern "C" {

const char* slang_version(void) { return "0.6.1-cpp"; }

// ABI 版本：签名或语义不兼容时递增，Python 侧会拒绝加载不匹配的库。
int slang_abi_version(void) { return 2; }

const char* slang_last_error(void) { return g_error.c_str(); }

char* slang_lex(const char* src, size_t len, int mode, size_t* out_len) {
    g_error.clear();
    std::string raw = src ? std::string(src, len) : std::string();
    std::string s = normalize_newlines(raw);
    Lexer lexer(s, mode);
    if (!lexer.run()) return nullptr;

    std::string payload = "[";
    payload += lexer.out;
    payload += "]";

    char* buf = (char*)malloc(payload.size() + 1);
    if (!buf) return nullptr;
    memcpy(buf, payload.data(), payload.size());
    buf[payload.size()] = '\0';
    if (out_len) *out_len = payload.size();
    return buf;
}

void slang_free(char* p) { free(p); }

}  // extern "C"
