# Có gì mới

Mọi thay đổi đáng chú ý của dytool. Tải bản cài ở trang
[Releases](https://github.com/snopvsreal/dytool/releases) — từ 0.3.2 trở đi app tự báo và tự cài bản mới
(Cài đặt › Cập nhật).

Ký hiệu: ✨ mới · 🎨 giao diện · 🛠️ sửa lỗi · ⚡ nhanh hơn

---

## [0.4.0] — 2026-11-26

**Thanh trạng thái dưới đáy, menu kênh bật / tắt nhanh, trang Hôm nay gọn hơn**

- 🎨 **Thanh trạng thái dưới đáy cửa sổ**: Hệ thống (ổn định / N việc cần xử lý) · Douyin · Tải Douyin · Giọng nói.
  Số phiên bản nằm cạnh tên app ở đầu menu.
- ✨ **Menu kênh**: bấm ảnh đại diện để bật / tắt kênh (ảnh xám = đang tắt), bấm tên để mở Cài đặt kênh. Bấm chữ
  "Kênh" mở trang **Tất cả kênh** (tìm, bật / tắt nhanh). Nhiều kênh thì menu chỉ hiện 4 kênh đầu + "N kênh khác".
- 🎨 **Trang Hôm nay**: bỏ ô tổng quan và dây chuyền video, dải video chờ duyệt to hơn; Lịch hôm nay và Kênh chia đôi
  cột phải.
- ✨ Danh sách **Chờ edit**: ảnh + tên kênh, lọc theo kênh, nút *Edit ngay*.
- 🛠️ Video đã tải chỉ có tiếng, hình đen (file gốc H.265) → phát bản H.264 từ Douyin.
- 🛠️ Xem video gốc Douyin: hiện "Đang lấy video…" khi chờ và lý do cụ thể khi lỗi.
- Bỏ phím tắt Ctrl+1–6 / Ctrl+K.

## [0.3.13] — 2026-11-26

- 🎨 **Trang Hôm nay vừa 1 màn hình**, không phải cuộn (cửa sổ từ 1181 × 700).
- Video chờ duyệt thành 1 dải ngang tự co theo chiều cao cửa sổ, lăn chuột để lướt; video tự duyệt có nhãn *Tự duyệt*.
- Lịch hôm nay / Kênh tự cuộn bên trong; **Nhật ký** thành nút mở cửa sổ nhỏ.

## [0.3.12] — 2026-11-26

- 🎨 Bỏ khối *Đang làm / Tiếp theo* khỏi trang Hôm nay — bấm thẻ **Chạy tự động** để mở hộp: trạng thái, việc đang làm
  (từng bước, nút **Dừng**), danh sách việc tiếp theo đúng thứ tự. Hộp tự cập nhật 2 giây / lần.

## [0.3.11] — 2026-11-26

- 🛠️ Nút thu nhỏ / phóng to / đóng sát đúng mép phải cửa sổ.

## [0.3.10] — 2026-11-26

- 🎨 Nút cửa sổ nằm sát góc trên phải như app Windows; vẫn kéo 4 mép để đổi kích thước, phóng to rồi thu về giữ đúng
  kích thước cũ.

## [0.3.9] — 2026-11-26

- ✨ **Cửa sổ nổi hướng dẫn captcha Douyin** luôn nổi ở mép phải màn hình: tự nhận ra dạng captcha (kéo ảnh, xoay ảnh,
  mảnh ghép, bấm chữ theo thứ tự, bấm vào ảnh, mã tin nhắn), chỉ hiện 2–3 bước cho đúng dạng đó kèm câu hỏi đã dịch.
- Nút **Mở cửa sổ Douyin** / **Đã giải xong**, giải xong tự đóng. Xem thử: Cài đặt › Douyin › *Xem thử cửa sổ hướng
  dẫn*. dytool không tự giải captcha.

## [0.3.8] — 2026-11-26

- ✨ **Tự gắn proxy tĩnh** cho tài khoản Facebook / TikTok mới lần đầu Đăng nhập — mỗi hồ sơ 1 proxy khác nhau, kiểm
  tra trước, proxy chết thì thử cái kế. Không đụng hồ sơ đã có proxy / đã đăng nhập.
- ✨ **Proxy xoay**: mẫu sẵn Proxy.vn, TMProxy, KiotProxy hoặc link API bất kỳ; cổng cố định + link đổi IP; cổng tự
  xoay. Nút **Đổi IP** (kiểm tra luôn, hiện IP mới). Proxy riêng cho tìm / tải Douyin, tuỳ chọn tự đổi IP khi gặp
  captcha. Key chỉ lưu trên máy.

## [0.3.7] — 2026-11-25

- ✨ **Thêm nhiều nguồn 1 lần** — mỗi dòng 1 từ khoá / 1 link kênh, xem trước bản dịch tiếng Trung từng dòng.
- ✨ **Quét mọi thời gian**, mặc định xếp theo nhiều like, 50 video / lần; nguồn Kênh lấy mọi video của kênh.
- ✨ Video bị lọc ghi rõ lý do ("ít like (101 < 1.000)", "quá dài (186 s > 180 s)").
- ✨ **Xem video gốc trước khi tải** (không lưu file, tua được).
- ✨ Tự nhận ra captcha Douyin, mở douyin.com để giải, tạm ngừng tìm (tải / edit vẫn chạy); câu hỏi captcha dịch sang
  tiếng Việt.
- *Quét nguồn ngay* chạy cả khi tắt Chạy tự động; *Edit ngay* dùng được cho video "Tìm thấy".
- 🎨 Bỏ bo góc toàn app, chế độ Sáng có menu trái sáng; Sáng / Tối / Tự động chuyển vào Cài đặt › Giao diện, không
  còn nháy khi đổi.

## [0.3.6] — 2026-11-25

- ✨ **Giọng nói — 3 tab mới**: *Đọc hàng loạt* (mỗi dòng 1 file + file ghép + .zip), *Lồng tiếng video* (video tiếng
  Trung bất kỳ trên máy, theo Cài đặt kênh), *Giọng → chữ* (.txt + .srt). Thêm ~600 ngôn ngữ OmniVoice.
- ✨ **Trình duyệt ShardX**: dấu vân tay Windows, macOS, Linux và Android; mục **Khởi động** mới (tần số quét, QUIC,
  ẩn tên hồ sơ, WebRTC…).

## [0.3.5] — 2026-11-25

- ✨ **Lồng tiếng không cần cài VoiceStudio** — model OmniVoice chạy ngầm ngay trong dytool, tốc độ bằng VoiceStudio.
  Model 3,3 GB tải 1 lần, kiểm tra mã băm; giọng đã lưu và từ điển phát âm được chép sang, giữ nguyên tên.
- ✨ **Trình cài mới** cùng kiểu với app: liệt kê từng thứ sẽ tải (dung lượng, nguồn, thứ đã có), tiến độ từng file,
  tốc độ, nút Thử lại.
- ✨ Màn hình khởi động bật sẵn bộ tải Douyin và máy chủ giọng nói; máy chủ giọng nói tự tắt khi rảnh để trả VRAM.
- ⚡ Khởi động nhanh, nhẹ máy hơn; ảnh đại diện kênh; tự lấy link bài đã đăng; sửa đăng TikTok Studio giao diện mới.

## [0.3.4] — 2026-11-21

- 🛠️ Bấm Cập nhật trong app mà dytool biến mất, không cập nhật — đã sửa ở cả trình cài lẫn app.
- 🛠️ Cài lại thư viện không còn đổi bản GPU về CPU trên máy có card NVIDIA.

## [0.3.3] — 2026-11-21

- ✨ Tự xử lý máy chặn chạy chương trình trong AppData (lỗi "side-by-side") — chép nhân trình duyệt ra `C:\ShardX`.
- ⚡ Trang Cài đặt mở trong ~0,2 giây (trước ~1,3 giây); thanh trạng thái làm mới ~0,03 giây (trước ~4 giây).

## [0.3.2] — 2026-11-21

- ✨ **Tự cập nhật trong app** (kiểm tra SHA256, cài và mở lại).
- ✨ Trang **Cài đặt** chung; **tăng tốc bằng card NVIDIA** (tách giọng nhanh ~3×); tự cài WebView2 nếu thiếu.
- 🛠️ Soát toàn bộ app: không đăng trùng / đăng nhầm, chặn trang web lạ điều khiển dytool, lịch đăng luôn theo giờ
  Việt Nam, video không còn kẹt, tự dọn file cũ, không chặn Windows tắt máy.

## [0.3.1] — 2026-11-21

- Trình cài: trang Hoàn tất có ô mở ShardBrowser / VoiceStudio lần đầu.

## [0.3.0] — 2026-11-21

- ✨ **Bản app Windows đầu tiên**: trình cài kiểu Windows 11, cửa sổ riêng, thu xuống khay.
- ✨ **TikTok**: kênh riêng hoặc đăng kèm cùng giờ với Page Facebook.
- ✨ Mỗi tài khoản 1 hồ sơ trình duyệt chống phát hiện riêng, tự tạo khi Đăng nhập.

[0.4.0]: https://github.com/snopvsreal/dytool/releases/tag/v0.4.0
[0.3.13]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.13
[0.3.12]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.12
[0.3.11]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.11
[0.3.10]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.10
[0.3.9]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.9
[0.3.8]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.8
[0.3.7]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.7
[0.3.6]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.6
[0.3.5]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.5
[0.3.4]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.4
[0.3.3]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.3
[0.3.2]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.2
[0.3.1]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.1
[0.3.0]: https://github.com/snopvsreal/dytool/releases/tag/v0.3.0
