import pypdf
import sys

def extract_text_from_pdf(pdf_path, output_txt_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        text = []
        # Extract text from the first 10 pages or all if less than 10
        # Usually executive summary and key points are at the beginning
        num_pages = min(len(reader.pages), 15) 
        
        for i in range(num_pages):
            page = reader.pages[i]
            text.append(f"--- Page {i+1} ---")
            text.append(page.extract_text())
            
        with open(output_txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(text))
            
        print(f"Successfully extracted text to {output_txt_path}")
        
    except Exception as e:
        print(f"Error extracting text: {e}")
        sys.exit(1)

if __name__ == "__main__":
    pdf_file = "data/input/260121-GS-中国股市的反弹能否提振消费者支出.pdf"
    txt_file = ".gemini/tmp/extracted_report.txt"
    extract_text_from_pdf(pdf_file, txt_file)

