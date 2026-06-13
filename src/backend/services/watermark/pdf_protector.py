import fitz
import os
import logging
import uuid
import random

logger = logging.getLogger(__name__)

import datetime

class PDFProtector:
    """
    V4 Service - 'West Lake Vinegar Fish' (西湖醋鱼) Defensive Mode:
    - Dynamic Content: '西湖醋鱼' + Current Date.
    - Random Corner Toggling: Top-Right vs Bottom-Right.
    - High-Entropy Jitter: Random spacing, color, and size per page.
    - Ghosting Layer: Low-opacity center watermark to prevent cropping.
    - AES-256 Lockdown.
    """

    @staticmethod
    def add_watermark_and_protect(input_path, output_path, watermark_text=None, owner_pw=None):
        if not owner_pw:
            owner_pw = str(uuid.uuid4())

        # Default content if not provided
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        main_content = str(watermark_text or f"西湖醋鱼 {today_str}")

        try:
            doc = fitz.open(input_path)

            for page in doc:
                rect = page.rect
                width, height = rect.width, rect.height

                # 1. Floating Corner Watermark (Top-Right vs Bottom-Right)
                is_top = random.choice([True, False])
                # Significant margin_x increase to accommodate ~250px text width
                # Starts 320-420px from right edge, ensuring it ends ~100px before edge
                margin_x = 360 + random.randint(-50, 50)
                margin_y = 80 + random.randint(-25, 25)

                if is_top:
                    base_point = fitz.Point(width - margin_x, margin_y)
                else:
                    base_point = fitz.Point(width - margin_x, height - margin_y)

                # Randomized Style (Color, Spacing, and now Rotation)
                chars = list(main_content)
                base_color_val = random.uniform(0.40, 0.65)
                base_color = (base_color_val, base_color_val, base_color_val)
                base_fontsize = random.randint(19, 25)
                base_rotation = random.randint(-8, 8) # Floating tilt

                for i, char in enumerate(chars):
                    char_spacing = i * (base_fontsize * 0.78) + random.randint(-4, 4)
                    char_point = base_point + (char_spacing, 0)

                    page.insert_text(
                        point=char_point,
                        text=char,
                        fontsize=base_fontsize,
                        fontname="china-ss",
                        color=base_color,
                        fill_opacity=random.uniform(0.22, 0.48),
                        morph=(char_point, fitz.Matrix(base_rotation))
                    )

                # 2. Intermittent Ghost Layer (Anti-Crop & Anti-Algorithm)
                # Only 65% chance of appearing to break temporal consistency
                if random.random() < 0.65:
                    ghost_point = fitz.Point(width/2 + random.randint(-60, 60), height/2 + random.randint(-60, 60))
                    ghost_angle = random.randint(30, 60)
                    page.insert_text(
                        point=ghost_point,
                        text=main_content,
                        fontsize=60,
                        fontname="china-ss",
                        color=(0.7, 0.7, 0.7),
                        fill_opacity=random.uniform(0.04, 0.08),
                        morph=(ghost_point, fitz.Matrix(ghost_angle))
                    )

            # 4. Permissions Lockdown
            perm = int(fitz.PDF_PERM_ACCESSIBILITY)
            doc.save(
                output_path,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw=owner_pw,
                user_pw=None,
                permissions=perm,
                garbage=2,
                deflate=True
            )
            doc.close()
            return True, owner_pw

        except Exception as e:
            logger.error(f"V4 Protection failed: {str(e)}")
            return False, str(e)

if __name__ == "__main__":
    # Quick internal test if run directly
    pass
