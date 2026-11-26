# Có gì mới

Mọi thay đổi đáng chú ý của dytool. Tải bản cài ở trang
[Releases](https://github.com/snopvsreal/dytool/releases) — từ 0.3.2 trở đi app tự báo và tự cài bản mới
(Cài đặt › Cập nhật).

Ký hiệu: ✨ mới · 🎨 giao diện · 🛠️ sửa lỗi · ⚡ nhanh hơn

---

## [0.5.6] — 2026-11-27

**Giọng lồng tiếng đọc thong thả, nghỉ rõ giữa câu + giữ tiếng mèo cả lúc đang đọc + tự giảm nhạc nền**

- 🛠️ **Giọng đọc không còn dồn**: trước đây dytool tính theo tốc độ người đọc TikTok nhanh và ép đọc nhanh thêm cho vừa khung, nên chỗ nghỉ bị nén mất. Giờ có **Tốc độ đọc** ở Cài đặt kênh › Edit & lồng tiếng: Chậm rãi / **Vừa** (mặc định) / Nhanh (như cũ). Bản dịch được viết gọn cho vừa tốc độ đó thay vì đọc ép, và giữa các câu có chỗ nghỉ thật (~0,6 giây).
- 🛠️ **Tiếng mèo còn cả lúc đang lồng tiếng**: dytool dò từng khúc có tiếng người và chỉ tắt đúng khúc đó — tiếng mèo xen giữa các câu nói, hay lúc giọng Việt đang đọc, vẫn nghe được.
- ✨ **Tự giảm nhạc nền** (bật sẵn, tắt ở Cài đặt kênh): nhạc nền to hơn giọng đọc thì hạ xuống, và nhỏ đi lúc giọng Việt đang đọc, hết câu thì lên lại.
- Video đã edit trước đây: mở video › **Edit lại** để có cách đọc / trộn âm mới.

## [0.5.5] — 2026-11-27

**Nút "Edit lại" cho video đã edit nhưng chưa đăng**

- ✨ **Edit lại**: mở 1 video Chờ duyệt / Đã duyệt / chờ gửi lịch (bấm video ở trang Hôm nay, trang kênh hoặc trang Video) → bấm **Edit lại** → video được edit lại theo cài đặt kênh hiện tại (vd. để có lại tiếng mèo sau bản 0.5.4, hoặc sau khi đổi giọng), bản edit cũ được thay, xong thì về Chờ duyệt để duyệt lại. Video đang đăng / đã đăng / đã hẹn giờ trên nền tảng không có nút này (tránh đăng trùng); video đã bị dọn file gốc cũng không edit lại được.

## [0.5.4] — 2026-11-27

**Lồng tiếng giữ lại tiếng mèo kêu, tiếng động gốc + hết lỗi "máy chủ giọng nói không trả lời" khi đọc bằng CPU**

- 🛠️ **Không còn mất tiếng mèo**: bước tách giọng xếp tiếng mèo kêu, tiếng cười, tiếng hát vào phần "giọng" nên trước đây bị bỏ cùng giọng Trung. Giờ dytool giữ lại tiếng gốc, chỉ tắt đúng những đoạn có người nói (mép mờ dần, không nghe "bụp"). Bật sẵn cho mọi kênh; tắt ở Cài đặt kênh › Edit & lồng tiếng nếu muốn bỏ hẳn. Video đã edit trước đây: bấm Edit lại để có tiếng.
- 🛠️ **Sửa lỗi "Máy chủ giọng nói không trả lời: timed out"** khi đọc bằng CPU: dytool chờ mỗi câu tới 30 phút (trước 5 phút) và không gửi lại câu khi máy chủ còn đang đọc. Bấm Dừng có tác dụng ngay cả lúc đang chờ; bảng tiến trình hiện "đang đọc N s".
- ✨ Nhật ký trang Giọng nói ghi thời gian đọc từng câu (đọc bao nhiêu giây giọng mất bao lâu, trên card hay CPU).

## [0.5.3] — 2026-11-27

**Edit lỗi bấm Thử lại thì chạy tiếp từ chỗ lỗi, không làm lại từ đầu**

- ⚡ **Lưu kết quả từng bước edit**: dò vị trí phụ đề Trung, nghe giọng (Whisper), đọc phụ đề trên video (OCR), tách giọng, nhận giọng nam / nữ, dịch, và từng câu lồng tiếng. Edit lỗi (hoặc bấm Dừng) rồi Thử lại → bước nào xong rồi thì dùng lại, chỉ làm tiếp phần còn thiếu — ví dụ lỗi ở câu lồng tiếng thứ 6 thì lần sau đọc tiếp từ câu 6. Bảng tiến trình ghi "dùng lại kết quả lần trước".
- Đổi cài đặt ảnh hưởng tới kết quả (giọng, cách dịch…) hoặc tải lại video thì bước đó tự làm lại; đổi kiểu chữ phụ đề (font, cỡ, vị trí) không mất bản dịch. Edit xong thì xoá kết quả đã lưu — bấm Edit lại vẫn dịch / đọc mới.

## [0.5.2] — 2026-11-27

**Lồng tiếng chạy được trên máy có card đồ hoạ hay lỗi + chọn chạy bằng CPU hoặc card đồ hoạ**

- 🛠️ **Sửa lỗi 500 "illegal memory access"** (gặp trên RTX 3050 Ti): lỗi này làm hỏng card cho cả máy chủ giọng nói nên bản 0.5.1 chuyển CPU vẫn lỗi. Giờ máy chủ giọng nói tự khởi động lại chạy bằng CPU, dytool chờ nạp xong (khoảng 1 phút) rồi đọc lại đúng câu đó — video không bị lỗi. Máy nhớ chạy CPU trong 24 giờ.
- ✨ **Chọn chạy lồng tiếng bằng gì** ở Cài đặt › Dịch & lồng tiếng: **Tự động** (card đồ hoạ, lỗi thì tự chuyển CPU), **Card đồ hoạ (GPU)** (luôn dùng card), **CPU** (không dùng card — cả tách giọng; chậm hơn, hợp máy có card hay lỗi). Đổi lúc giọng nói đang bật thì tự khởi động lại.
- 🛠️ Nút **Thử lại card đồ hoạ** ở trang Giọng nói giờ khởi động lại máy chủ giọng nói để dùng lại card thật sự.

## [0.5.1] — 2026-11-27

**Lồng tiếng không còn lỗi 500 khi card đồ hoạ trục trặc**

- 🛠️ **Card đồ hoạ lỗi thì tự đọc bằng CPU**: driver NVIDIA cũ, card không hợp thư viện hay hết bộ nhớ card trước đây làm lồng tiếng báo "500 Internal Server Error". Giờ máy chủ giọng nói tự chuyển sang CPU và đọc lại đúng câu đó — video vẫn được lồng tiếng (chậm hơn). Hết bộ nhớ card thì dọn bộ nhớ, thử lại card 1 lần trước.
- ✨ Trang **Giọng nói** báo khi đang chạy CPU vì card lỗi (kèm chi tiết kỹ thuật) và có nút **Thử lại card đồ hoạ** — bấm sau khi cập nhật driver.
- 🛠️ Lỗi lồng tiếng chưa lường trước giờ hiện đúng tên lỗi thay cho chữ "Internal Server Error" chung chung.

## [0.5.0] — 2026-11-26

**Mẫu edit kiểu CapCut cho từng kênh: intro / outro, chữ, logo, bộ lọc, nhạc — kéo thả**

- ✨ **Mẫu edit của kênh** (Cài đặt kênh › Edit & lồng tiếng › **Mở trình Mẫu edit**): cửa sổ riêng bố cục kiểu CapCut — thư viện, trình phát, thuộc tính, dòng thời gian. Làm 1 lần, tự lưu vào kênh; mọi video kênh edit sau đó theo mẫu (áp sau bước che sub / phụ đề / lồng tiếng).
- ✨ **Intro / outro bằng video bạn làm sẵn**: kéo video vào ô Intro / Outro (độ dài tự khớp, tối đa 30 s), giữ / tắt tiếng, vừa khung (cắt cho đầy, nền mờ, nền màu), 16 kiểu chuyển cảnh.
- ✨ **Chữ & logo** kéo thả lên khung xem (8 kiểu chữ có sẵn, viền, hộp nền, hoạt ảnh, thời gian hiện), **bộ lọc** (9 kiểu) và **chỉnh màu** (sáng, tương phản, bão hoà, nhiệt độ màu, làm nét, hạt phim, tối 4 góc, phóng to chậm dần), **nhạc nền**, **thanh tiến độ**, **khung hình** 9:16 / 1:1 / 4:5 / 16:9, cắt đầu / cuối video bằng cách kéo mép trên dòng thời gian, tốc độ.
- ✨ **Thả file từ máy** vào cửa sổ để thêm vào thư viện (video, ảnh, nhạc — dùng chung mọi kênh). Ctrl+Z / Ctrl+Y hoàn tác, Delete xoá, Ctrl+D nhân đôi lớp.
- ✨ **Render thử** xuất bản thật bằng FFmpeg trên video xem thử: video đã edit / đã tải của kênh, video tải lên, hoặc **video mẫu dựng sẵn 9:16** (luôn có, kể cả khi app vừa cài). **Chép mẫu** sang kênh khác.
- 🎨 Lật ngang / nghiêng / màu phủ của kênh chuyển vào trình Mẫu edit; trang Cài đặt kênh chỉ còn thẻ tóm tắt mẫu.

## [0.4.2] — 2026-11-26

**App nhẹ hơn khi đang chạy tự động**

- ⚡ **App nhẹ hơn khi đang chạy tự động**: trang không còn vẽ lại mỗi 3 giây chỉ vì có dòng nhật ký mới — đo trên trang Hôm nay với 40 video chờ duyệt: CPU của cửa sổ giảm từ ~16% xuống ~1% một nhân, số lần vẽ lại trang 13 → 4 lần / 40 giây.
- ⚡ Video chờ duyệt hiện bằng **ảnh thu nhỏ** thay cho trình phát video: cửa sổ không phải mở lại và giải mã từng file video mỗi lần cập nhật (số phần tử trang giảm ~10 lần).

## [0.4.1] — 2026-11-26

**Trang Hướng dẫn trong app**

- ✨ **Trang Hướng dẫn** (menu Hệ thống): mục **Bắt đầu** liệt kê các bước thiết lập lần đầu — AI, Douyin, tài khoản, nguồn, bật kênh, model giọng nói, Chạy tự động, duyệt video đầu tiên — tự đánh dấu bước đã xong, nút đi thẳng tới chỗ cần làm. Bên dưới là hướng dẫn theo chủ đề (quy trình 1 video, nguồn & lọc, duyệt + phím tắt, lịch đăng, lồng tiếng, TikTok, captcha, proxy, chạy tự động, cập nhật & sao lưu) kèm mục lục.
- 🛠️ Trang kênh: nhãn trạng thái dài ("Đã lên lịch trên Facebook") không còn đè lên cột Thời điểm.

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

[0.4.2]: https://github.com/snopvsreal/dytool/releases/tag/v0.4.2
[0.4.1]: https://github.com/snopvsreal/dytool/releases/tag/v0.4.1
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
