CXX ?= c++
CXXFLAGS ?= -O2 -fPIC -std=c++17 -Wall -Wextra

UNAME := $(shell uname -s 2>/dev/null)
ifeq ($(UNAME),Darwin)
  LIBNAME := libslangcore.dylib
  SHARED := -dynamiclib
else
  LIBNAME := libslangcore.so
  SHARED := -shared
endif

LIB := build/$(LIBNAME)

all: $(LIB)

build:
	mkdir -p build

$(LIB): slang_core.cpp | build
	$(CXX) $(CXXFLAGS) $(SHARED) -o $@ slang_core.cpp

clean:
	rm -rf build

.PHONY: all clean
