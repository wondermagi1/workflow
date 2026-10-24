def average(numbers):
    """返回 numbers 的算术平均值。

    参数:
        numbers: 数字列表（或其他支持 sum/len 的序列）。

    返回:
        空序列返回 None；非空序列返回 float 类型的算术平均值。

    异常:
        TypeError: 传入 None 或不支持 len 的可迭代对象时由内置函数抛出。
    """
    if not numbers:
        return None
    return sum(numbers) / len(numbers)
