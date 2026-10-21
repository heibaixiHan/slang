.PHONY: test test-python core clean

# test 依赖 core：先确保 C++ 核心是最新的，再跑测试
test: core
	python3 -m unittest discover -s tests -v

# 只跑纯 Python（显式禁用 C++ 核心，跳过 4 个 C++ 专属测试）
test-python:
	SLANG_CORE=/nonexistent/libslangcore.so python3 -m unittest discover -s tests -v

core:
	$(MAKE) -C core

clean:
	$(MAKE) -C core clean
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete
