# Báo cáo cá nhân - Day 9 Multi-Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Nguyễn Thanh Hoàn |
| MSSV / MHV |01201 |
| Lớp / Khóa | K3 |
| Vai trò chính | Thiết kế pipeline multi-agent, policy engine, verifier, trace và metadata |
| Ngày hoàn thành | 2026-08-05 |

## 2. Bài toán nghiệp vụ

Bài lab yêu cầu xây dựng hệ thống multi-agent để xử lý 50 khiếu nại thương mại điện tử dựa trên dữ liệu Olist. Mỗi case cần được đối chiếu với nhiều nguồn dữ liệu: đơn hàng, item, seller, thanh toán và mốc giao hàng. Kết quả cuối cùng phải xác định đúng vấn đề chính, entity liên quan, nguyên nhân gốc, bằng chứng, khoản hoàn tiền đề xuất và hành động xử lý.

Điểm khó của nghiệp vụ là cùng một nội dung khiếu nại, ví dụ "giao hàng trễ", có thể dẫn tới nhiều kết luận khác nhau. Nếu seller bàn giao hàng cho đơn vị vận chuyển sau hạn `shipping_limit_date`, trách nhiệm thuộc seller. Nếu seller bàn giao đúng hạn nhưng khách nhận hàng sau `estimated_delivery_date`, trách nhiệm thuộc logistics. Nếu dữ liệu cho thấy đơn được giao đúng hạn, claim giao trễ bị bác bỏ. Ngoài ra, các đơn `canceled` hoặc `unavailable` đã thanh toán phải được ưu tiên xử lý hoàn tiền trước các rule giao hàng.

## 3. Phạm vi công việc đã thực hiện

| Hạng mục | File chính | Kết quả |
| --- | --- | --- |
| Data access | `src/dispute_resolution/data_store.py` | Load 50 case, index order, item, payment và seller từ CSV |
| Domain agents | `src/dispute_resolution/agents.py` | Tách OrderSellerAgent, PaymentAgent, DeliveryAgent, PolicyAgent |
| Policy engine | `src/dispute_resolution/policy.py` | Áp dụng `EC_POLICY_V1` theo đúng thứ tự ưu tiên |
| LLM reviewer | `src/dispute_resolution/llm.py` | Gọi `gemini-2.5-flash-lite` để review độc lập |
| Coordinator | `src/dispute_resolution/pipeline.py` | Điều phối agent, ghi output, trace, metadata và zip |
| Verification | `src/dispute_resolution/validation.py` | Kiểm schema, entity, evidence, financial trước khi ghi file |
| Tests | `tests/test_pipeline.py`, `tests/test_llm.py` | Kiểm regression, integration và parser LLM |

## 4. Thiết kế multi-agent

Hệ thống được thiết kế theo hướng deterministic-first. Các agent xử lý nghiệp vụ bằng code và handoff facts có cấu trúc cho agent tiếp theo. LLM không sinh output cuối cùng, chỉ review quyết định đã có.

Luồng xử lý:

```text
Input EC_*.json
-> CoordinatorAgent
-> OrderSellerAgent
-> PaymentAgent
-> DeliveryAgent
-> PolicyAgent
-> build_output()
-> GeminiPolicyReviewerAgent
-> VerifierAgent
-> output/EC_*.json
-> trace.jsonl + metadata.json + output.zip
```

Vai trò chính:

- `CoordinatorAgent`: đọc input, gọi các agent, ghi trace và chỉ ghi output khi verifier pass.
- `OrderSellerAgent`: lấy trạng thái order, item, seller, tổng item, tổng freight và seller bàn giao trễ.
- `PaymentAgent`: cộng toàn bộ payment row, phát hiện split payment, đối soát với item + freight trong sai số 0.10 BRL.
- `DeliveryAgent`: xác định đơn giao trễ hay không dựa trên `order_delivered_customer_date` và `order_estimated_delivery_date`.
- `PolicyAgent`: áp dụng sáu rule nghiệp vụ theo priority của README.
- `GeminiPolicyReviewerAgent`: dùng Gemini để kiểm tra độc lập quyết định, chỉ ghi kết quả review vào trace.
- `VerifierAgent`: kiểm output trước khi ghi file, gồm schema, entity, evidence và financial.

## 5. Quy tắc nghiệp vụ đã triển khai

Policy được áp dụng theo đúng thứ tự ưu tiên:

| Thứ tự | Primary issue | Điều kiện | Refund | Action |
| --- | --- | --- | --- | --- |
| 1 | `canceled_order_paid` | Order `canceled` và đã thanh toán | Tổng payment | `issue_full_refund` |
| 2 | `unavailable_order_paid` | Order `unavailable` và đã thanh toán | Tổng payment | `issue_full_refund` |
| 3 | `late_delivery_seller` | Giao trễ và seller bàn giao sau hạn | Tổng freight | `refund_freight` |
| 4 | `late_delivery_logistics` | Giao trễ, seller bàn giao đúng hạn | Tổng freight | `refund_freight` |
| 5 | `valid_split_payment` | Có nhiều payment row và tổng tiền khớp | 0 | `explain_valid_split_payment` |
| 6 | `unsupported_late_claim` | Không giao trễ và payment khớp | 0 | `reject_late_refund` |

Các phép tính tiền dùng `Decimal`, làm tròn 2 chữ số thập phân. `payment_value` được cộng theo từng payment row, không nhân với installments. Với order không có item row, `item_ids` và `seller_ids` để rỗng, `item_total_brl` và `freight_total_brl` bằng `0.0`.

## 6. Chiến lược evidence

Evidence chỉ dùng ID có thể dựng trực tiếp từ dữ liệu hoặc policy:

```text
order:<order_id>
item:<order_id>:<order_item_id>
payment:<order_id>:<payment_sequential>
seller:<seller_id>
policy:<root_cause_code>
```

Tôi chọn evidence theo nguyên tắc đủ để truy vết nhưng không đưa ID dư:

- Luôn có `order:<order_id>` vì mọi case bắt đầu từ order.
- Có `item:*` cho các order có item row, vì item và freight tạo ra financial fields.
- Có `payment:*` cho mọi payment row dùng để tính `payment_total_brl`.
- Có `seller:*` chỉ khi seller là responsible party trong `late_delivery_seller`.
- Luôn có `policy:<cause_code>` để chỉ rõ rule tạo quyết định.

Verifier tính lại expected evidence từ facts và decision. Nếu output có evidence thiếu, sai định dạng, không tồn tại hoặc dư không liên quan, pipeline sẽ fail trước khi ghi file. Chế độ `--validate-only` cũng tải lại CSV và chạy lại policy để phát hiện output bị sửa sau khi pipeline hoàn tất.

## 7. Vai trò của Gemini

Model được khai báo là `gemini-2.5-flash-lite`, gọi qua Google Generative Language API bằng `GOOGLE_API_KEY` trong `.env`.

Gemini không phải agent quyết định output chính. Trong pipeline, output được dựng trước khi gọi Gemini. Gemini chỉ nhận facts đã chuẩn hóa và `PolicyDecision`, sau đó trả về `agree` hoặc `disagree` cùng lý do ngắn. Kết quả này được ghi vào `logging/trace.jsonl` dưới event `independent_review`.

Thiết kế này giúp bài lab vẫn có thành phần LLM/API, nhưng tránh rủi ro LLM hallucinate ID, tính sai tiền, đoán evidence hoặc thay đổi kết quả không ổn định. Nếu API lỗi, pipeline deterministic vẫn sinh output từ dữ liệu CSV và verifier.

Lưu ý: Google không công bố public parameter count của `gemini-2.5-flash-lite`, nên metadata ghi rõ `parameter_count = not publicly disclosed` và `compliance_status = not independently verifiable`.

## 8. Trace, metadata và provenance

Mỗi case ghi 8 event vào `logging/trace.jsonl`:

```text
case_received
OrderSellerAgent handoff_completed
PaymentAgent handoff_completed
DeliveryAgent handoff_completed
PolicyAgent decision_completed
GeminiPolicyReviewerAgent independent_review
VerifierAgent verification_passed
CoordinatorAgent output_written
```

Event `output_written` lưu file output, SHA-256 của file, action đã ghi và cờ `llm_modified_output=false`. `metadata.json` lưu thông tin model, runtime, framework, số case, số output, distribution issue, trạng thái API key, bảo mật secret và digest chung của 50 output.

Điểm quan trọng về chống can thiệp: output được sinh bởi `src/dispute_resolution/pipeline.py` từ `PolicyDecision`; LLM reviewer không được truyền vào `build_output`. Vì vậy output có thể tái sinh lại từ source code và dữ liệu, sau đó đối chiếu hash với trace.

## 9. Kiểm chứng

Các lệnh kiểm tra:

```powershell
python -m unittest discover -s tests -v
python run.py --with-llm --zip
python run.py --validate-only
```

Kết quả kỳ vọng:

- 7 test pass.
- Sinh đúng 50 file JSON từ `output/EC_001.json` đến `output/EC_050.json`.
- `output.zip` chứa đúng 50 JSON, không có file lạ.
- `logging/trace.jsonl` có 400 event cho 50 case.
- `logging/metadata.json` ghi `case_count = 50`, `output_count = 50`.
- Distribution issue hiện tại: 8 canceled, 8 unavailable, 8 late seller, 8 late logistics, 9 valid split payment, 9 unsupported late claim.

## 10. Blocker và cách xử lý

Ban đầu có cân nhắc dùng Gemma E4B để đáp ứng rõ yêu cầu model dưới 10B tham số. Tuy nhiên model này không gọi được trực tiếp qua Gemini hosted API bằng model ID đã thử. Vì vậy pipeline chuyển sang `gemini-2.5-flash-lite` để thực hiện bước LLM reviewer, đồng thời ghi rõ trong metadata rằng parameter count không thể xác minh độc lập.

Một vấn đề khác là điểm evidence ban đầu thấp hơn các nhóm chỉ số còn lại. Tôi đã điều chỉnh logic evidence theo hướng chỉ đưa seller evidence khi seller thật sự chịu trách nhiệm, đồng thời vẫn giữ item evidence cho các case có item row để chứng minh các trường tài chính item/freight. Sau thay đổi, verifier cũng được siết lại để evidence của output phải khớp contract này.

## 11. Kết luận cá nhân

Giải pháp cuối cùng ưu tiên tính đúng, khả năng tái lập và khả năng giải thích. Multi-agent không chỉ là đặt tên nhiều agent, mà là chia domain rõ ràng: order/seller, payment, delivery, policy, review và verification. Mỗi agent tạo handoff có cấu trúc, giúp trace đủ rõ để kiểm tra lại từng quyết định.

Phần tôi thấy quan trọng nhất là không để LLM quyết định các trường cần độ chính xác cao như tiền, ID bằng chứng và responsible party. Với bài toán này, policy đã được README mô tả rõ, nên deterministic engine là source-of-truth hợp lý hơn. Gemini được dùng như lớp review độc lập để tăng khả năng kiểm tra, không làm mất tính ổn định của output.

## 12. Cam kết

- [x] Báo cáo phản ánh đúng pipeline đã triển khai.
- [x] Không ghi API key, token hoặc secret vào báo cáo.
- [x] Output được sinh bằng code, không chỉnh tay từng JSON.
- [x] Có trace, metadata và hash để kiểm chứng provenance.
- [x] Có thể chạy lại pipeline để tái sinh output.
- [x] Đã điền họ tên và 5 số cuối MHV trước khi nộp.

**Họ và tên:** Nguyen Thanh Hoan

**Ngày xác nhận:** 2026-08-05
