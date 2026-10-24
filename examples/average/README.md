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
