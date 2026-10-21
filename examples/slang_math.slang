# examples/slang_math.slang —— 供 slang_demo.slang 导入的模块
def pi = 3.14159

def square(x) = x * x
def cube(x) = x * x * x

def sum_squares(xs) =
    reduce((fn (a, b) = a + b), 0, map(square, xs))
