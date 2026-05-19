#!/usr/bin/env python3
"""
Run the CC + TSI folder pipeline.

Purpose:
    Keep the historical example entrypoint stable while the reusable
    implementation lives in ``dasQt.core.dispersion_components.ccf``.

Typical run:
    uv run python /Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/caculate_cc_tsi_from_folder.py \
        --config /Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/config/cc_config.jsonc

Output:
    Grouped ``.mat`` files and optional ``*_diag.json`` diagnostics under
    ``--output-dir``.

The CLI arguments are defined by
``dasQt.core.dispersion_components.ccf.folder_pipeline.run_cc_tsi_folder_pipeline``
and remain compatible with the previous script.
"""

from __future__ import annotations

from dasQt.core.dispersion_components.ccf.folder_pipeline import run_cc_tsi_folder_pipeline


def main() -> None:
    run_cc_tsi_folder_pipeline()


if __name__ == "__main__":
    main()
