# `.slang` 人类友好格式演示
# 运行：python3 -m slang run examples/slang_demo.slang
# 解码：python3 -m slang decode examples/slang_demo.slang
import "slang_math.slang" as math
from "slang_math.slang" import square

def fib(n):
    if n < 2:
        n
    else:
        fib(n - 1) + fib(n - 2)

def classify(n):
    if n < 0:
        "negative"
    elif n == 0:
        "zero"
    else:
        "positive"

def greet(name):
    println(f"hello, {name}!")

greet("slang")
println(f"fib(15) = {fib(15)}")
println(f"square(9) = {square(9)}, math.cube(3) = {math.cube(3)}")
println(f"classify(-2/0/5) = {classify(-2)}, {classify(0)}, {classify(5)}")

xs = [1, 2, 3, 4, 5]
println(f"xs = {xs}")
println(f"sum = {reduce((fn (a, b) = a + b), 0, xs)}")
println(f"evens = {filter((fn (x) = x % 2 == 0), xs)}")
println(f"math.pi = {math.pi}")
