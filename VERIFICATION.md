# V1.2.0 版本驗證與未結項目

資料：達天MP test log.zip；核准報告 V2.6.1 為版型/口徑比對參考。

## 已實測
- Python compileall：PASS。
- pytest：10/10 PASS（MES/功能 FAIL 排除、RF PCB ID、FAIL/複測排序、CSV不覆寫LOG、32-sheet Excel）。
- MP ZIP 動態重新解析：3,653 筆 LOG 事件，RF LOG 量測 343,269 筆，CSV 1,791 份（輔助），CSV行 377,901。
- 輸出 32 張 Excel 表，ZIP CRC PASS；OOXML 解析 PASS；00_整體品質總覽 veryHidden PASS。
- 缺失機台識別碼的耦合事件：7 筆，另列資料品質問題而非擅自歸屬。
- RF PCB ID 不會被量測分組鍵覆寫；RF sheet 前排為 PCB ID。

## 未達成、不得宣稱通過
- 核准 V2.6.1 全工作表逐格數據一致：NOT PASS。16A / 26 / 27 / 29 等專項表仍需逐筆 Parser。
- 3,662 vs 3,653 計數口徑差異未完成正式對帳；不能直接認定是9筆漏LOG。
- GitHub Actions Windows runner EXE Build：NOT RUN HERE（GitHub 遠端需確認）。

## 交付原則
V1.2.0 是實作+回歸測試版本，但仍是驗證版。未結項不得以空白或預設 PASS 冒充正式結果。
