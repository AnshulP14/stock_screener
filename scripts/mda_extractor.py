"""
MD&A and Qualitative Section Extractor

Extracts Management Discussion & Analysis and other qualitative sections
from PDF Annual Reports for AI agent consumption.

Uses keyword anchoring to locate sections in Indian filings.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Section Keywords for Indian Annual Reports (SEBI Format)
# =============================================================================

MDA_SECTION_HEADERS = [
    r"Management\s*Discussion\s*(?:and|&)\s*Analysis",
    r"Management['']s\s*Discussion\s*(?:and|&)\s*Analysis",
    r"MD\s*&\s*A",
    r"MDA\s+Report",
    r"Business\s+Overview\s+and\s+Outlook",
]

CHAIRMAN_SPEECH_HEADERS = [
    r"Chairman['']?s?\s+(?:Message|Letter|Speech|Address)",
    r"Letter\s+(?:to|from)\s+(?:the\s+)?Shareholders?",
    r"From\s+the\s+Chairman['']?s?\s+Desk",
]

CEO_MESSAGE_HEADERS = [
    r"(?:CEO|MD)['']?s?\s+(?:Message|Letter|Review)",
    r"Managing\s+Director['']?s?\s+(?:Message|Report)",
    r"Message\s+from\s+(?:the\s+)?(?:CEO|Managing\s+Director)",
]

RISK_SECTION_HEADERS = [
    r"Risk\s+Management",
    r"Risks?\s+(?:and|&)\s+Concerns?",
    r"Risk\s+Factors?",
    r"Enterprise\s+Risk\s+Management",
]

OUTLOOK_HEADERS = [
    r"(?:Business\s+)?Outlook",
    r"Future\s+(?:Outlook|Prospects?)",
    r"Going\s+Forward",
    r"Way\s+Forward",
]

STRATEGY_HEADERS = [
    r"(?:Business\s+)?Strategy",
    r"Strategic\s+(?:Initiatives?|Priorities?|Direction)",
    r"Our\s+Strategy",
]

# Section end markers
SECTION_END_MARKERS = [
    r"Directors['']?\s+Report",
    r"Corporate\s+Governance",
    r"Report\s+on\s+Corporate\s+Governance",
    r"Auditors?['']?\s+Report",
    r"Financial\s+Statements?",
    r"Notes\s+to\s+(?:the\s+)?Financial\s+Statements?",
    r"Balance\s+Sheet",
    r"Statement\s+of\s+Profit\s+(?:and|&)\s+Loss",
    r"Annexure",
]


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ExtractedSection:
    """A single extracted qualitative section."""

    title: str
    content: str
    page_start: int | None = None
    page_end: int | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "confidence": self.confidence,
        }


@dataclass
class QualitativeReport:
    """Complete qualitative extraction from an annual report."""

    company_name: str = ""
    fiscal_year: str = ""
    source_file: str = ""
    mda_section: ExtractedSection | None = None
    chairman_speech: ExtractedSection | None = None
    ceo_message: ExtractedSection | None = None
    risk_discussion: ExtractedSection | None = None
    outlook: ExtractedSection | None = None
    strategy: ExtractedSection | None = None
    additional_sections: list[ExtractedSection] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "company_name": self.company_name,
            "fiscal_year": self.fiscal_year,
            "source_file": self.source_file,
            "sections": {
                "mda": self.mda_section.to_dict() if self.mda_section else None,
                "chairman_speech": (
                    self.chairman_speech.to_dict() if self.chairman_speech else None
                ),
                "ceo_message": (
                    self.ceo_message.to_dict() if self.ceo_message else None
                ),
                "risk_discussion": (
                    self.risk_discussion.to_dict() if self.risk_discussion else None
                ),
                "outlook": self.outlook.to_dict() if self.outlook else None,
                "strategy": self.strategy.to_dict() if self.strategy else None,
                "additional": [s.to_dict() for s in self.additional_sections],
            },
        }

    def get_combined_text(self) -> str:
        """Get all extracted text combined for AI analysis."""
        parts = []
        for section in [
            self.chairman_speech,
            self.ceo_message,
            self.mda_section,
            self.strategy,
            self.outlook,
            self.risk_discussion,
        ]:
            if section:
                parts.append(f"## {section.title}\n\n{section.content}")

        for section in self.additional_sections:
            parts.append(f"## {section.title}\n\n{section.content}")

        return "\n\n---\n\n".join(parts)


# =============================================================================
# PDF Text Extraction (with fallback options)
# =============================================================================


def _extract_text_pypdf(pdf_path: Path) -> tuple[str, list[tuple[int, int, str]]]:
    """Extract text using pypdf (formerly PyPDF2)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pypdf not installed. Run: pip install pypdf")

    reader = PdfReader(pdf_path)
    full_text = []
    page_markers = []  # (char_start, char_end, page_num)

    char_offset = 0
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        start = char_offset
        full_text.append(text)
        char_offset += len(text) + 1  # +1 for newline
        page_markers.append((start, char_offset, i))

    return "\n".join(full_text), page_markers


def _extract_text_pdfplumber(pdf_path: Path) -> tuple[str, list[tuple[int, int, str]]]:
    """Extract text using pdfplumber (better for complex layouts)."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber not installed. Run: pip install pdfplumber")

    full_text = []
    page_markers = []
    char_offset = 0

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            start = char_offset
            full_text.append(text)
            char_offset += len(text) + 1
            page_markers.append((start, char_offset, i))

    return "\n".join(full_text), page_markers


def extract_pdf_text(pdf_path: str | Path) -> tuple[str, list[tuple[int, int, int]]]:
    """
    Extract text from PDF with page markers.

    Returns:
        Tuple of (full_text, page_markers) where page_markers is
        list of (char_start, char_end, page_number)
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    # Try pdfplumber first (better quality), fallback to pypdf
    try:
        return _extract_text_pdfplumber(pdf_path)
    except ImportError:
        logger.info("pdfplumber not available, using pypdf")
        return _extract_text_pypdf(pdf_path)


# =============================================================================
# Section Extraction Logic
# =============================================================================


def _find_section(
    text: str,
    header_patterns: list[str],
    end_patterns: list[str] | None = None,
    max_length: int = 50000,
) -> tuple[str, int | None, int | None, float] | None:
    """
    Find a section in text using header patterns.

    Returns:
        Tuple of (content, start_pos, end_pos, confidence) or None
    """
    if end_patterns is None:
        end_patterns = SECTION_END_MARKERS

    # Compile patterns
    header_re = re.compile(
        "|".join(f"({p})" for p in header_patterns), re.IGNORECASE | re.MULTILINE
    )
    end_re = re.compile(
        "|".join(f"({p})" for p in end_patterns), re.IGNORECASE | re.MULTILINE
    )

    # Find section start
    match = header_re.search(text)
    if not match:
        return None

    start_pos = match.start()
    section_title = match.group(0).strip()

    # Find section end
    search_start = match.end()
    end_match = end_re.search(text, search_start + 500)  # Skip at least 500 chars

    if end_match:
        end_pos = end_match.start()
    else:
        end_pos = min(start_pos + max_length, len(text))

    content = text[search_start:end_pos].strip()

    # Calculate confidence based on content length and structure
    confidence = 1.0
    if len(content) < 500:
        confidence = 0.5
    elif len(content) < 1000:
        confidence = 0.7

    return section_title, content, start_pos, end_pos, confidence


def _get_page_number(
    char_pos: int, page_markers: list[tuple[int, int, int]]
) -> int | None:
    """Get page number for a character position."""
    for start, end, page in page_markers:
        if start <= char_pos < end:
            return page
    return None


# =============================================================================
# Main Extraction Functions
# =============================================================================


class MDAExtractor:
    """Extracts qualitative sections from PDF annual reports."""

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        self.text: str = ""
        self.page_markers: list[tuple[int, int, int]] = []

    def extract(self) -> QualitativeReport:
        """Extract all qualitative sections from the PDF."""
        logger.info(f"Extracting from: {self.pdf_path}")

        # Extract text
        self.text, self.page_markers = extract_pdf_text(self.pdf_path)

        report = QualitativeReport(source_file=str(self.pdf_path))

        # Extract company name and fiscal year from text
        report.company_name = self._extract_company_name()
        report.fiscal_year = self._extract_fiscal_year()

        # Extract each section type
        report.mda_section = self._extract_section(
            MDA_SECTION_HEADERS, "Management Discussion & Analysis"
        )
        report.chairman_speech = self._extract_section(
            CHAIRMAN_SPEECH_HEADERS, "Chairman's Message"
        )
        report.ceo_message = self._extract_section(CEO_MESSAGE_HEADERS, "CEO Message")
        report.risk_discussion = self._extract_section(
            RISK_SECTION_HEADERS, "Risk Management"
        )
        report.outlook = self._extract_section(OUTLOOK_HEADERS, "Business Outlook")
        report.strategy = self._extract_section(STRATEGY_HEADERS, "Strategy")

        return report

    def _extract_section(
        self, patterns: list[str], default_title: str
    ) -> ExtractedSection | None:
        """Extract a single section using patterns."""
        result = _find_section(self.text, patterns)
        if not result:
            return None

        title, content, start_pos, end_pos, confidence = result

        return ExtractedSection(
            title=title or default_title,
            content=self._clean_content(content),
            page_start=_get_page_number(start_pos, self.page_markers),
            page_end=_get_page_number(end_pos, self.page_markers) if end_pos else None,
            confidence=confidence,
        )

    def _clean_content(self, content: str) -> str:
        """Clean extracted content for AI consumption."""
        # Remove excessive whitespace
        content = re.sub(r"\n{3,}", "\n\n", content)
        content = re.sub(r" {2,}", " ", content)

        # Remove page numbers and footers
        content = re.sub(r"\n\d+\s*\n", "\n", content)
        content = re.sub(r"Page\s+\d+\s+of\s+\d+", "", content, flags=re.IGNORECASE)

        # Remove common artifacts
        content = re.sub(r"Annual\s+Report\s+\d{4}[-–]\d{2,4}", "", content, flags=re.IGNORECASE)

        return content.strip()

    def _extract_company_name(self) -> str:
        """Extract company name from the PDF text."""
        # Look for common patterns in Indian annual reports
        patterns = [
            r"([A-Z][A-Za-z\s&]+(?:Limited|Ltd\.?|Private\s+Limited))",
            r"Annual\s+Report\s+of\s+([A-Z][A-Za-z\s&]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, self.text[:5000])
            if match:
                return match.group(1).strip()

        return ""

    def _extract_fiscal_year(self) -> str:
        """Extract fiscal year from the PDF text."""
        patterns = [
            r'(?:FY|Financial\s+Year)\s*[:""]?\s*(\d{4}[-\u2013]\d{2,4})',
            r'(\d{4}[-\u2013]\d{2,4})\s*(?:Annual\s+Report)',
            r'Year\s+ended\s+(?:March|December)\s+\d{1,2},?\s+(\d{4})',
        ]

        for pattern in patterns:
            match = re.search(pattern, self.text[:5000], re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""


# =============================================================================
# High-Level API Functions
# =============================================================================


def extract_mda_from_pdf(pdf_path: str | Path) -> QualitativeReport:
    """
    Extract MD&A and qualitative sections from a PDF annual report.

    Args:
        pdf_path: Path to the PDF annual report

    Returns:
        QualitativeReport containing all extracted sections
    """
    extractor = MDAExtractor(pdf_path)
    return extractor.extract()


def extract_qualitative_text(pdf_path: str | Path) -> str:
    """
    Extract and combine all qualitative text for AI consumption.

    Args:
        pdf_path: Path to the PDF annual report

    Returns:
        Combined markdown-formatted text of all qualitative sections
    """
    report = extract_mda_from_pdf(pdf_path)
    return report.get_combined_text()


def extract_for_sentiment_analysis(pdf_path: str | Path) -> dict[str, Any]:
    """
    Extract sections optimized for sentiment/strategic analysis.

    Returns structured data with:
    - Management tone (chairman/CEO messages)
    - Strategic direction (strategy, outlook)
    - Risk awareness (risk discussion)
    - Operational commentary (MD&A)
    """
    report = extract_mda_from_pdf(pdf_path)

    return {
        "company": report.company_name,
        "fiscal_year": report.fiscal_year,
        "management_tone": {
            "chairman": report.chairman_speech.content if report.chairman_speech else None,
            "ceo": report.ceo_message.content if report.ceo_message else None,
        },
        "strategic_direction": {
            "strategy": report.strategy.content if report.strategy else None,
            "outlook": report.outlook.content if report.outlook else None,
        },
        "risk_awareness": report.risk_discussion.content if report.risk_discussion else None,
        "operational_commentary": report.mda_section.content if report.mda_section else None,
        "extraction_confidence": {
            "mda": report.mda_section.confidence if report.mda_section else 0,
            "chairman": report.chairman_speech.confidence if report.chairman_speech else 0,
            "ceo": report.ceo_message.confidence if report.ceo_message else 0,
            "risk": report.risk_discussion.confidence if report.risk_discussion else 0,
        },
    }


# =============================================================================
# CLI Interface
# =============================================================================


def main():
    """CLI entry point for MD&A extraction."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Extract MD&A and qualitative sections from PDF annual reports"
    )
    parser.add_argument("pdf", help="Path to PDF annual report")
    parser.add_argument("-o", "--output", help="Output JSON file path")
    parser.add_argument(
        "-t", "--text", action="store_true", help="Output combined text only"
    )
    parser.add_argument(
        "-s", "--sentiment", action="store_true", help="Output for sentiment analysis"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    if args.text:
        result = extract_qualitative_text(args.pdf)
        print(result)
    elif args.sentiment:
        result = extract_for_sentiment_analysis(args.pdf)
        print(json.dumps(result, indent=2))
    else:
        report = extract_mda_from_pdf(args.pdf)
        result = report.to_dict()

        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Saved to: {args.output}")
        else:
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
