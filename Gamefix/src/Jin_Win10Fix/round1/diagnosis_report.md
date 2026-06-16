# 项目阶段性进展报告

## 已完成事项

1. **Win10鼠标冻结BUG** — ✅ 已修复
   - 根因：`AttachThreadInput` + `ImmGetDefaultIMEWnd`/`ImmGetContext`/`ImmSetOpenStatus`
   - 方案：修改exe导入表（kernel32→user32→imm32→fix），fix.dll在DllMain中Hook 4个API
   - 验证：游戏窗口正常，鼠标操作不会导致冻结

2. **注册表弹窗** — 独立处理
   - 导入 `注册表.reg` 或手动创建 `HKLM\SOFTWARE\WOW6432Node\NitroPlus\塵骸魔京\serial`=`TAC31745`

## 当前进度：90%

## 存档文件（workspace/fix_dll/）

| 文件 | 用途 |
|------|------|
| `fix.dll` | Hook DLL (85KB PE32)，4个API |
| `fix.cpp` / `fix.def` | 源码 |
| `patch_exe.py` | 修补exe导入表脚本 |
| `_build_all.bat` | 一键编译脚本 |

## 部署

复制到游戏目录：`fix.dll` + `NitroSystem_patched.exe`（由patch_exe.py生成）
