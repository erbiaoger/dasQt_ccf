# 互相关示例配置

`cc_config.jsonc` 是互相关参数示例，支持注释。可从 GUI 加载，或交给现有文件夹处理入口。
`cc_backend` 仅选择 `cpu`、`cupy`、`torch`、`cpp_cuda`；`cc_device=auto` 由后端检测设备。
`cpp_cuda` 需要 CUDA 扩展，`cupy` 需要 CuPy 和 NVIDIA GPU。旧纯 C++ CPU 后端及
`cc_threads`、`cc_memory_budget_mb` 已删除。

时间窗批次和炮批次自动选择，无需填写 `cc_batch_chunks`、`cc_shot_batch_size`。
运行日志显示实际规划；详见 `docs/methods/cc_auto_batch.md`。

生产配置模板位于 `dasqt/config/cc_config.jsonc`，运行和方法说明见根 README
及 `docs/methods/cc_backend_contract.md`。此目录其他本地配置不作为本次交付模板。
