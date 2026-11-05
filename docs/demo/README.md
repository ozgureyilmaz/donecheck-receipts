# Receipts demo media

The canonical clip uses live run `20261105T170325-payment_declined-spark`: the overeager Spark actor said “Flight booked,” the live Spark judge returned `FALSE_DONE`, DOM checks returned `FALSE_DONE`, and SAM 3.1 boxed the declined-payment toast in frame 22 at 64.9687 seconds.

| File | Format | SHA-256 |
|---|---|---|
| [MP4](receipts-20261105T170325-payment_declined-spark.mp4) | H.264, 1920×1080, 8 seconds | `714b44ade8b24190e1c5545fa6ebd1983056897d1b955ece1dc048c29a99de0d` |
| [GIF](receipts-20261105T170325-payment_declined-spark.gif) | 960×540, 4 frames, 8 seconds | `ecb9519e58b81c3931332d728488f00dd1b070baaebb983618cce1b83903443e` |
| [Provenance](receipts-20261105T170325-payment_declined-spark.provenance.json) | Source and output hashes, live actor, judge, and SAM attempt | `468918df01714ec931ccb4e14a09b4f0acc7fd0bfe98cb4454d76cd3e27b458e` |

The video presents the claim, the untouched recorder screenshot with the original decline toast, the original and SAM overlay side by side, and the three-source verdict. The renderer checked the SAM attempt referenced by `evidence.json`, matched its parsed boxes to the saved overlay pixels, and recorded hashes for source artifacts including the original `video.webm`. It did not alter the source run.

The local renderer is in `runs/demo-renderer/`. This bundle contains the verified media and provenance manifest.
