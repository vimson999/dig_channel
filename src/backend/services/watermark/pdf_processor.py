import fitz  # type: ignore
import cv2
import numpy as np
from PIL import Image
import logging
from pathlib import Path
from typing import Tuple, Optional

# Configure module-level logger
logger = logging.getLogger(__name__)

class PDFProcessor:
    """
    Service class to handle PDF processing tasks, specifically watermark removal.
    """

    def __init__(self, threshold: int = 200):
        """
        Initialize the processor.
        
        :param threshold: Pixel brightness threshold (0-255). 
                          Pixels brighter than this in all channels will be whitened.
        """
        self.threshold = threshold

    def remove_watermark(self, input_path: str, output_path: str) -> Tuple[bool, str]:
        """
        Removes light-colored watermarks from a PDF by treating it as an image 
        and whitening pixels above a certain brightness threshold.

        :param input_path: Path to the source PDF.
        :param output_path: Path to save the processed PDF.
        :return: (Success: bool, Message: str)
        """
        doc = None
        try:
            logger.info(f"Starting processing for: {input_path}")
            
            if not Path(input_path).exists():
                return False, "Input file does not exist."

            doc = fitz.open(input_path)
            if len(doc) == 0:
                return False, "PDF is empty."

            # Strategy: Extract main image from the first page (Long-image PDF format)
            # Future improvement: Loop through all pages if it's a multi-page standard PDF.
            page = doc[0]
            images = page.get_images(full=True)
            
            if not images:
                return False, "No images found in the PDF to process."

            # Heuristic: The largest image is likely the content
            largest_img_bytes = self._extract_largest_image(doc, images)
            
            if not largest_img_bytes:
                return False, "Failed to extract valid image data."

            # Process Image
            success, processed_img = self._process_image_opencv(largest_img_bytes)
            if not success or processed_img is None:
                return False, "Image processing failed."

            # Save back to PDF
            self._save_image_as_pdf(processed_img, output_path)
            
            logger.info(f"Successfully saved processed PDF to: {output_path}")
            return True, "Success"

        except Exception as e:
            logger.error(f"Error processing PDF {input_path}: {str(e)}", exc_info=True)
            return False, f"Internal Error: {str(e)}"
        finally:
            if doc:
                doc.close()

    def _extract_largest_image(self, doc, images) -> Optional[bytes]:
        """Finds and returns the bytes of the largest image in the list."""
        largest_bytes = None
        max_size = 0
        
        for img in images:
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                if len(image_bytes) > max_size:
                    max_size = len(image_bytes)
                    largest_bytes = image_bytes
            except Exception as e:
                logger.warning(f"Failed to extract image xref {xref}: {e}")
                continue
                
        return largest_bytes

    def _process_image_opencv(self, img_bytes: bytes) -> Tuple[bool, Optional[np.ndarray]]:
        """Decodes bytes and applies the whitening algorithm."""
        try:
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is None:
                return False, None

            # Create mask for light pixels (likely watermarks/bg)
            lower_bound = np.array([self.threshold, self.threshold, self.threshold])
            upper_bound = np.array([255, 255, 255])
            
            mask = cv2.inRange(img, lower_bound, upper_bound)
            
            # Apply whitening
            img[mask > 0] = [255, 255, 255]
            
            return True, img
        except Exception as e:
            logger.error(f"OpenCV processing error: {e}")
            return False, None

    def _save_image_as_pdf(self, cv2_img: np.ndarray, output_path: str):
        """Converts OpenCV BGR image to PDF via Pillow."""
        img_rgb = cv2.cvtColor(cv2_img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        pil_img.save(output_path, "PDF", resolution=100.0)
