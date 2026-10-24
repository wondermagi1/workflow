# CodeFlow 交付报告

## 任务
修复平均值函数：空列表返回 None；非空列表返回算术平均值；不修改输入。请编写正常、空列表、负数和单元素的 unittest 测试。

语言：python · 来源：DeepSeek
状态：completed

## Agent 协作
- 需求规划师：现有 average 函数对空列表会因 len(numbers)==0 触发 ZeroDivisionError，且缺少空列表返回 None 的分支；函数本身不修改输入（sum 与 len 均为只读操作），但需在测试中显式断言输入未被修改。修复方案是在 solution.py 中为 average 增加空列表提前返回 None 的分支，非空时保持 sum/len 的算术平均值逻辑，并在 test_solution.py 中用 unittest 覆盖正常、空列表、负数、单元素四类用例，同时校验输入列表保持不变。
- 编码工程师：修复 average：空列表提前返回 None，非空列表返回 sum/len 的浮点算术平均值，全程只读不修改输入；并补充 unittest 测试覆盖正常、空列表、负数、单元素及输入不变性，附 README 说明。
- 代码审查师：修复实现与测试均符合验收标准：solution.py 中 average 对空列表提前返回 None，非空列表返回 sum/len 的浮点平均值，且仅使用只读操作不修改输入；test_solution.py 用 unittest 覆盖正常、空列表、负数、单元素四类场景并额外断言输入不变性，静态分析无语法或规则问题。测试尚未实际执行，运行状态为 unknown，需本地验证。

## 审查结论
通过 · 92 分
模型审查与静态分析不等于测试通过；测试尚未在服务端执行。
- [low] solution.py:13 空列表判断使用 if not numbers，对空元组、空字符串等也会返回 None，与文档中“列表输入”的约定略有出入；对 None 输入会抛 TypeError（已在 README 说明，属 MVP 可接受范围）。 建议：如需严格限定列表语义，可改为 if len(numbers) == 0 或显式校验 isinstance(numbers, (list, tuple))，并在文档中保持一致的输入约定。
- [low] test_solution.py:29 输入不变性测试仅覆盖非空列表与空列表，未覆盖负数或单元素场景下的不变性，且未验证返回值计算后原列表元素顺序未被改变（当前实现无风险，但断言强度可提升）。 建议：可在 test_input_not_modified 中增加负数列表与单元素列表的快照比较，或使用 assertListEqual 明确比较内容与顺序。
- [low] test_solution.py:9 测试未覆盖浮点精度场景（如 [0.1,0.2]），README 仅作提示而未在测试中体现 assertAlmostEqual 用法。 建议：可选增加一个使用 assertAlmostEqual 的浮点用例，或在 README 中给出示例代码，便于调用方参考。

## solution.py
``````python
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

``````

## test_solution.py
``````python
import unittest

from solution import average


class TestAverage(unittest.TestCase):
    """average 函数的正常、空列表、负数、单元素与输入不变性测试。"""

    def test_normal(self):
        """正常列表返回浮点算术平均值。"""
        self.assertEqual(average([1, 2, 3]), 2.0)
        self.assertEqual(average([1, 2]), 1.5)
        self.assertIsInstance(average([1, 2, 3]), float)

    def test_empty(self):
        """空列表返回 None，且不抛 ZeroDivisionError。"""
        self.assertIsNone(average([]))

    def test_negative(self):
        """负数与正负混合场景结果正确。"""
        self.assertEqual(average([-1, -2, -3]), -2.0)
        self.assertEqual(average([-5, 5]), 0.0)

    def test_single(self):
        """单元素返回该元素本身，类型为 float。"""
        self.assertEqual(average([7]), 7.0)
        self.assertIsInstance(average([7]), float)

    def test_input_not_modified(self):
        """调用后传入列表的内容与长度保持不变。"""
        data = [3, 1, 2]
        snapshot = list(data)
        average(data)
        self.assertEqual(data, snapshot)
        self.assertEqual(len(data), len(snapshot))

        empty = []
        average(empty)
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()

``````

## README.md
``````markdown
# average 函数修复说明

## 行为约定

- `average([])` 返回 `None`（不再抛 `ZeroDivisionError`）。
- 非空列表返回 `sum(numbers) / len(numbers)`，类型为 `float`。
- 函数不修改输入列表，仅使用 `sum` 与 `len` 等只读操作。
- 传入 `None` 或不支持 `len` 的可迭代对象会抛 `TypeError`，MVP 范围内仅约定列表输入。

## 测试覆盖

`test_solution.py` 使用标准库 `unittest`，包含：

- `test_normal`：`[1,2,3] -> 2.0`、`[1,2] -> 1.5`，并断言返回类型为 `float`。
- `test_empty`：`[] -> None`。
- `test_negative`：`[-1,-2,-3] -> -2.0`、`[-5,5] -> 0.0`。
- `test_single`：`[7] -> 7.0`。
- `test_input_not_modified`：调用前后列表内容与长度一致。

## 运行方式

```bash
python -m unittest -v
```

注意：浮点精度场景（如 `[0.1, 0.2]`）建议在调用方使用 `assertAlmostEqual` 比较。

``````

## 本地验证命令（未自动执行）
- `python -m unittest -v`
- `python -m unittest test_solution.TestAverage -v`
