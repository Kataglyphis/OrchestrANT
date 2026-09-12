# bench_docs_probe summary

Run: `/tmp/claude-1000/-home-jonas-GitHub-Kataglyphis-ANTfrastructure/8ac2595b-42be-44a3-b364-ad8bb0ffec2a/scratchpad/benchdocs/results/20260907-142306` — 2026-09-07 18:57:55

style=glm rows measure recognition + this harness's deterministic extraction; style=chat rows measure the model's own extraction. CUT/ERR excluded from pass_rate.

```
endpoint       family                   n  PASS%  metric  TTFT_s   tot_s  CUT  ERR
----------------------------------------------------------------------------------
glm-ocr        absent_field             5    100   1.000  293.36  334.30    0    0
glm-ocr        absent_field_twin        3    100   1.000    0.85    4.13    0    0
glm-ocr        kie_invoice_de           5    100   1.000  305.42  347.05    0    0
glm-ocr        kie_invoice_de_twin      3      0   0.714    0.79    5.37    0    0
glm-ocr        table_csv                5      0   0.000  293.55  318.82    0    0
glm-ocr        table_csv_twin           3      0   0.000    0.83    2.51    0    0
glm-ocr        transcribe_de            5    100   0.010  293.13  328.20    0    0
glm-ocr        transcribe_de_twin       3      0   0.384    0.87    5.90    0    0
qwen3-vl-4b    absent_field             5    100   1.000  399.26  445.58    0    0
qwen3-vl-4b    absent_field_twin        3    100   1.000    6.68   25.72    0    0
qwen3-vl-4b    kie_invoice_de           5    100   1.000  400.03  458.92    0    0
qwen3-vl-4b    kie_invoice_de_twin      3    100   1.000    6.80   32.07    0    0
qwen3-vl-4b    table_csv                5    100   1.000  400.64  484.63    0    0
qwen3-vl-4b    table_csv_twin           3    100   1.000    5.12   31.99    0    0
qwen3-vl-4b    transcribe_de            5    100   0.009  398.28  496.99    0    0
qwen3-vl-4b    transcribe_de_twin       3      0   0.022    3.58   30.52    0    0
```

## Per-case grid

| case | glm-ocr | qwen3-vl-4b |
|---|---|---|
| absent_field_s1 | PASS 1.000 | PASS 1.000 |
| absent_field_s1_jpeg50 | PASS 1.000 | PASS 1.000 |
| absent_field_s1_rot1 | PASS 1.000 | PASS 1.000 |
| absent_field_s1_twin | PASS 1.000 | PASS 1.000 |
| absent_field_s2 | PASS 1.000 | PASS 1.000 |
| absent_field_s2_twin | PASS 1.000 | PASS 1.000 |
| absent_field_s3 | PASS 1.000 | PASS 1.000 |
| absent_field_s3_twin | PASS 1.000 | PASS 1.000 |
| kie_invoice_de_s1 | PASS 1.000 | PASS 1.000 |
| kie_invoice_de_s1_jpeg50 | PASS 1.000 | PASS 1.000 |
| kie_invoice_de_s1_rot1 | PASS 1.000 | PASS 1.000 |
| kie_invoice_de_s1_twin | FAIL 0.714 | PASS 1.000 |
| kie_invoice_de_s2 | PASS 1.000 | PASS 1.000 |
| kie_invoice_de_s2_twin | FAIL 0.857 | PASS 1.000 |
| kie_invoice_de_s3 | PASS 1.000 | PASS 1.000 |
| kie_invoice_de_s3_twin | FAIL 0.571 | PASS 1.000 |
| table_csv_s1 | FAIL 0.000 | PASS 1.000 |
| table_csv_s1_jpeg50 | FAIL 0.000 | PASS 1.000 |
| table_csv_s1_rot1 | FAIL 0.000 | PASS 1.000 |
| table_csv_s1_twin | FAIL 0.000 | PASS 1.000 |
| table_csv_s2 | FAIL 0.000 | PASS 1.000 |
| table_csv_s2_twin | FAIL 0.000 | PASS 1.000 |
| table_csv_s3 | FAIL 0.000 | PASS 1.000 |
| table_csv_s3_twin | FAIL 0.000 | PASS 1.000 |
| transcribe_de_s1 | PASS 0.010 | PASS 0.010 |
| transcribe_de_s1_jpeg50 | PASS 0.010 | PASS 0.010 |
| transcribe_de_s1_rot1 | PASS 0.010 | PASS 0.010 |
| transcribe_de_s1_twin | FAIL 0.135 | FAIL 0.022 |
| transcribe_de_s2 | PASS 0.011 | PASS 0.009 |
| transcribe_de_s2_twin | FAIL 0.020 | FAIL 0.022 |
| transcribe_de_s3 | PASS 0.009 | PASS 0.009 |
| transcribe_de_s3_twin | FAIL 0.995 | FAIL 0.022 |
