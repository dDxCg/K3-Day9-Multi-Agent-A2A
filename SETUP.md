# Setup và cách chạy

## 1. Môi trường

Máy này chỉ có Python qua launcher `py` (không có `python` trên PATH).

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Key

```powershell
Copy-Item .env.example .env
```

Điền `GROQ_API_KEY` (hoặc `OPENROUTER_API_KEY` rồi đổi `LLM_PROVIDER=openrouter`).
`.env` đã nằm trong `.gitignore`. **Tên model không đặt trong `.env`** — đề yêu cầu khai
báo trong source, xem `MODEL_CATALOG` ở [src/config.py](src/config.py).

## 3. Chạy

```powershell
.\.venv\Scripts\python.exe -m src.main --case EC_001      # 1 case, in stdout
.\.venv\Scripts\python.exe -m src.main --all --limit 5    # smoke test
.\.venv\Scripts\python.exe -m src.main --all              # 50 case, ghi output/ + logging/
```

`--all` xoá `logging/trace.jsonl` rồi ghi lại (đề yêu cầu trace chỉ chứa lượt chạy mới
nhất), và ghi `logging/metadata.json`.

## 4. Cây thư mục

```
src/
  config.py       model catalog (cưỡng chế trần 10B), đường dẫn, sampling
  schema.py       pydantic output schema + bảng mapping EC_POLICY_V1 + helper evidence ID
  data_store.py   nạp CSV Olist, dựng OrderBundle cho một order
  llm.py          client OpenAI-compatible (Groq/OpenRouter), ép JSON, retry, trace
  tracing.py      trace.jsonl + metadata.json
  state.py        CaseState của graph, reducer merge findings
  graph.py        wiring LangGraph
  main.py         CLI
  agents/         coordinator, order_seller, payment, delivery, policy, verifier
```

## 5. Việc còn lại

| Việc | File | Ghi chú |
| --- | --- | --- |
| Xác định seller bàn giao muộn | `src/agents/order_seller.py` | so `order_delivered_carrier_date` với `shipping_limit_date` từng item |
| Đối soát payment | `src/agents/payment.py` | sai số `PAYMENT_TOLERANCE_BRL = 0.10` |
| So mốc giao hàng | `src/agents/delivery.py` | không đổi múi giờ |
| Phân loại issue + tính refund | `src/agents/policy.py` | `_classify`, `_refund_brl`, và điền entity/evidence |
| Nối LLM vào domain agent | các file trên | dùng `llm.call_json`, `SYSTEM_PROMPT` đã có sẵn |
| Điền báo cáo cá nhân | `individual_5SoCuoiMHV_HoVaTen.md` | template còn nguyên placeholder |

## 6. Nộp bài

Chỉ zip `output/` (đúng 50 file `EC_001.json` … `EC_050.json`, không kèm file lạ).
Commit source trước khi nộp zip. `*.zip` đã bị `.gitignore` chặn khỏi repo.
