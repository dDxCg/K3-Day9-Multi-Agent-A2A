# Báo cáo cá nhân — Day 9 Multi-Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | [ĐIỀN HỌ VÀ TÊN] |
| MSSV | [ĐIỀN MSSV] |
| Khóa/Lớp | K3 |
| Vai trò chính | Multi-agent pipeline, policy engine và verification |
| Ngày hoàn thành | 2026-08-05 |

## 2. Phạm vi công việc

| Module | File phụ trách | Input | Output | Trạng thái |
| --- | --- | --- | --- | --- |
| Data access | `src/dispute_resolution/data_store.py` | Olist CSV, 50 case | Case-scoped indexes | Hoàn thành |
| Domain agents | `src/dispute_resolution/agents.py` | Case và data handoff | Order/payment/delivery facts | Hoàn thành |
| Policy | `src/dispute_resolution/policy.py` | Agent facts | Issue, cause, refund, action | Hoàn thành |
| Verification | `src/dispute_resolution/validation.py` | Candidate output | Pass hoặc lỗi cụ thể | Hoàn thành |
| Orchestration | `src/dispute_resolution/pipeline.py` | 50 case | Output, trace, metadata, zip | Hoàn thành |
| Tests | `tests/test_pipeline.py` | Pipeline và dataset | Regression result | Hoàn thành |

## 3. Vấn đề kỹ thuật giải quyết

Pipeline phải phân biệt cùng một claim giao trễ thành lỗi seller, lỗi logistics
hoặc claim không được dữ liệu hỗ trợ. Ngoài ra phải ưu tiên canceled/unavailable
đã thanh toán trước các rule delivery, cộng đủ nhiều payment row và không tạo
evidence không tồn tại.

Giải pháp dùng các agent theo domain và handoff JSON có cấu trúc. Policy Agent
không đọc CSV trực tiếp; chỉ ra quyết định từ facts đã chuẩn hóa. Verifier tính
lại entity, evidence và số tiền trước khi Coordinator ghi file.

## 4. Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `input/EC_001.json` đến `EC_050.json` |
| Dữ liệu | orders, order_items, order_payments, sellers |
| Output | 50 JSON đúng schema README |
| Handoff | case ID, order ID, facts, calculations, evidence |
| Lỗi bị chặn | thiếu order, policy lạ, schema sai, evidence giả, financial mismatch |

## 5. Quyết định kỹ thuật quan trọng

- **Bối cảnh:** LLM có thể tính sai tiền, bỏ payment row hoặc hallucinate ID.
- **Phương án cân nhắc:** để LLM sinh toàn bộ output; hoặc deterministic policy
  kết hợp LLM review.
- **Phương án chọn:** deterministic-first, Gemini review độc lập.
- **Lý do:** policy đã được đặc tả rõ; code cho kết quả tái lập và kiểm chứng
  được. LLM vẫn tham gia quy trình nhưng không có quyền sửa source-of-truth.
- **Bằng chứng:** integration test sinh đủ 50 output và đúng distribution
  8/8/8/8/9/9.

## 6. Lỗi hoặc blocker đã xử lý

- **Triệu chứng:** `google/gemma-4-E4B-it` không có trong model list của Gemini
  API dù API key hợp lệ.
- **Nguyên nhân:** E4B là open-weight model dành cho local runtime; endpoint
  hosted hiện tại không expose model ID đó.
- **Xử lý:** dùng `gemini-2.5-flash-lite` cho API reviewer, giữ toàn bộ quyết
  định nghiệp vụ trong deterministic engine.
- **Giới hạn còn lại:** Google không công bố parameter count của model hosted;
  metadata ghi rõ không thể xác minh độc lập điều kiện 10B.

## 7. Kiểm chứng

```powershell
python -m unittest discover -s tests -v
python run.py --with-llm --zip
python run.py --validate-only
```

- Kỳ vọng: 4 test pass, 50 output JSON, 400 trace event, zip 50 entry.
- Artifact: `output/`, `output.zip`, `logging/trace.jsonl`,
  `logging/metadata.json`.

## 8. Hiểu biết end-to-end

Coordinator đọc `claimed_order_id`. Order/Seller Agent lấy order và items;
Payment Agent gom mọi payment row; Delivery Agent đối chiếu mốc giao; Policy
Agent áp dụng sáu rule theo priority; Gemini reviewer kiểm tra độc lập; Verifier
đối chiếu schema, evidence và financials; Coordinator mới ghi output.

Chất lượng được đo bằng regression distribution, unit/integration tests, số
output, evidence tồn tại, financial reconciliation và validation của toàn bộ
output directory. Cùng một test set phải được dùng cho mọi lần chạy để so sánh
được kết quả và phát hiện regression.

## 9. Cam kết

- [x] Nội dung kỹ thuật phản ánh đúng pipeline đã triển khai.
- [x] Có thể giải thích luồng end-to-end và từng handoff.
- [x] Không ghi kết quả thành công nếu chưa kiểm chứng.
- [x] Báo cáo không chứa API key, token hoặc secret.
- [ ] Đã thay thông tin họ tên và MSSV trước khi nộp.

**Họ và tên:** [ĐIỀN HỌ VÀ TÊN]

**Ngày xác nhận:** 2026-08-05
