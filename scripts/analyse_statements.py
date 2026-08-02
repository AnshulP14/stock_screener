#!/usr/bin/env python3
"""Analyse last N years of 10-K annual reports for trends, sentiment, pivots, confidence."""

import re
import os
import sys
import argparse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict


# --- Section extraction ---

def extract_sections_htm(filepath):
    """Extract key sections from SEC HTML filing."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()

    # Strip HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()

    sections = {}
    # Section anchors — search case-insensitively
    anchors = [
        ('business', r'Item 1\. Business'),
        ('risk', r'Item 1A\. Risk Factors'),
        ('mda', r'Item 7\. Management[\' ]*s Discussion'),
        ('market_risk', r'Item 7A\.'),
        ('forward', r'forward-looking statements'),
    ]

    positions = []
    for name, pattern in anchors:
        pos = re.search(pattern, text, re.IGNORECASE)
        if pos:
            positions.append((name, pos.start(), pos.end()))

    for i, (name, pos, _) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        sections[name] = text[pos:end][:20000]

    return sections, text


def extract_text_dclg(filepath):
    """Extract all text from DocLang XML (fallback — flat, no structure)."""
    tree = ET.parse(filepath)
    root = tree.getroot()
    texts = []
    for elem in root.iter():
        if elem.tag == 'text' and elem.text and elem.text.strip():
            texts.append(elem.text.strip())
    return ' '.join(texts)


# --- Theme tracking ---

THEMES = {
    'ai': r'(?:Apple Intelligence|AI features|artificial intelligence|generative AI|machine learning)',
    'spatial': r'(?:Vision Pro|spatial computer|visionOS|mixed reality)',
    'services': r'(?:services revenue|services segment|subscription.*service)',
    'china': r'(?:Greater China|china mainland)',
    'india': r'(?:(?:manufactur|sourcing|India).*(?:India)|(?:India).*(?:manufactur|supply))',
    'supply_chain': r'(?:supply chain|supplier|contract manufactur)',
    'tariff': r'(?:tariff|trade war|import restriction|U\.S\. Tariffs)',
    'regulatory': r'(?:antitrust|DMA|regulatory.*action|investigation|DOJ)',
    'privacy': r'(?:privacy|data protection|encryption)',
    'sustainability': r'(?:carbon neutral|environmental|sustainability|ESG|recycled component)',
    'competition': r'(?:competition|competitor|competitive)',
}


def count_themes(text, themes=None):
    """Count mentions of each theme in text."""
    if themes is None:
        themes = THEMES
    counts = {}
    for name, pattern in themes.items():
        counts[name] = len(re.findall(pattern, text, re.IGNORECASE))
    return counts


# --- Sentiment analysis ---

POSITIVE_MARKERS = [
    r'record\s+(?:revenue|sales|quarter|year)',
    r'all-time\s+high',
    r'strong\s+(?:growth|demand|performance)',
    r'(?:significantly|substantially)\s+(?:increased|grew|rose)',
    r'accelerat',
    r'robust',
    r'healthy\s+demand',
    r'momentum',
    r'expanding',
    r'outperform',
    r'exceeded\s+expectations',
    r'beating\s+estimates',
]

NEGATIVE_MARKERS = [
    r'materially\s+adversely',
    r'material\s+adverse',
    r'headwinds',
    r'(?:decline|decrease|contraction|downturn)',
    r'(?:challenging|difficult|uncertain)',
    r'uncertainty',
    r'volatile|volatility',
    r'adverse\s+impact',
    r'disruptions?',
    r'(?:risks|risk\s+factors)',
    r'(?:slowdown|weakness)',
]


def count_sentiment(text, markers=None, negative=None):
    """Count positive and negative language markers."""
    if markers is None:
        markers = POSITIVE_MARKERS
    if negative is None:
        negative = NEGATIVE_MARKERS

    pos = sum(len(re.findall(m, text, re.IGNORECASE)) for m in markers)
    neg = sum(len(re.findall(m, text, re.IGNORECASE)) for m in negative)
    return pos, neg


# --- Confidence signals ---

def extract_confidence_signals(text):
    """Extract signals of confidence vs caution in forward-looking statements."""
    signals = {
        'certain': [],  # "we expect", "we will", "we anticipate"
        'hedged': [],   # "we may", "there is uncertainty", "we cannot assure"
        'product_maturity': [],  # dropped "first", now "the"
    }

    # Certain language
    certain_patterns = [
        r'we\s+(?:expect|anticipate|project|intend)\s+to',
        r'we\s+will',
        r'we\s+plan\s+to',
        r'we\s+believe\s+that',
    ]
    # Hedged language
    hedged_patterns = [
        r'we\s+may\s+(?:not|be\s+able)',
        r'there\s+is\s+(?:uncertainty|no\s+assurance)',
        r'we\s+cannot\s+assure',
        r'may\s+materially',
        r'could\s+materially',
    ]
    # Product maturity: "the" without "first"
    maturity_patterns = [
        r'(?:Apple\s+Vision\s+Pro|the\s+spatial\s+computer|visionOS)',
    ]

    sentences = re.split(r'(?<=[.!?])\s+', text)
    for sent in sentences:
        sent_lower = sent.lower()
        for p in certain_patterns:
            if re.search(p, sent_lower):
                if len(sent) > 50:
                    signals['certain'].append(sent[:200])
                    break
        for p in hedged_patterns:
            if re.search(p, sent_lower):
                if len(sent) > 50:
                    signals['hedged'].append(sent[:200])
                    break
        for p in maturity_patterns:
            if re.search(p, sent_lower):
                # Check if "first" is nearby
                context = sent_lower
                if 'first' not in context:
                    signals['product_maturity'].append(sent[:200])

    return signals


# --- Risk categories ---

RISK_CATS = {
    'geopolitical': r'(?:geopolitical|tariff|trade.*dispute|sanction)',
    'currency': r'(?:currency.*fluctuat|forex|renminbi|FX\s|rmb)',
    'supply': r'(?:supply chain|supplier|component|manufactur)',
    'competition': r'(?:competition|competitor|market share)',
    'regulatory': r'(?:regulatory|antitrust|investigation|litigation)',
    'macro': r'(?:recession|inflation|slowdown|economic)',
    'cyber': r'(?:cyber|security\s+breach|data\s+breach|hack)',
    'key_person': r'(?:co-found.*death|steve jobs)',
}


def count_risk_categories(text):
    """Count mentions of each risk category."""
    counts = {}
    for name, pattern in RISK_CATS.items():
        counts[name] = len(re.findall(pattern, text, re.IGNORECASE))
    return counts


# --- Year extraction from filename ---

def extract_year(filename):
    """Extract year from filename like AAPL_10K_2025.htm."""
    m = re.search(r'10K_(\d{4})', filename)
    return int(m.group(1)) if m else None


# --- Main analysis ---

def analyse_company(symbol, base_dir, years=5):
    """Analyse last N years of 10-Ks for a company."""

    company_dir = os.path.join(base_dir, symbol)
    if not os.path.isdir(company_dir):
        print(f"Error: {company_dir} not found", file=sys.stderr)
        return

    # Find .htm files
    htm_files = []
    for f in os.listdir(company_dir):
        if f.endswith('.htm'):
            yr = extract_year(f)
            if yr:
                htm_files.append((yr, os.path.join(company_dir, f)))

    if not htm_files:
        print(f"Error: no .htm files found for {symbol}", file=sys.stderr)
        return

    # Sort by year, get last N
    htm_files.sort(key=lambda x: x[0])
    htm_files = htm_files[-years:]

    if not htm_files:
        print(f"No files to analyse for {symbol}", file=sys.stderr)
        return

    years_list = [yr for yr, _ in htm_files]

    # Per-year data
    per_year = {}
    for yr, fpath in htm_files:
        sections, full_text = extract_sections_htm(fpath)

        per_year[yr] = {
            'sections': sections,
            'full_text': full_text,
            'themes': count_themes(full_text),
            'sentiment': count_sentiment(full_text),
            'risk': count_risk_categories(full_text),
            'confidence': extract_confidence_signals(full_text),
            'word_count': len(full_text.split()),
        }

    return symbol, years_list, per_year


def format_report(symbol, years, data):
    """Format analysis as terse report."""
    lines = []
    lines.append(f"=== {symbol} — 5-YEAR STATEMENT ANALYSIS (FY{'–'.join(str(y) for y in years)}) ===\n")

    # Word count trend
    word_counts = [data[y]['word_count'] for y in years]
    lines.append(f"REPORT LENGTH: {' → '.join(f'{y}: {wc:,} words' for y, wc in zip(years, word_counts))}\n")

    # --- THEME EVOLUTION ---
    all_themes = set()
    for yr in years:
        all_themes.update(data[yr]['themes'].keys())

    theme_matrix = {}
    for theme in sorted(all_themes):
        values = []
        for yr in years:
            values.append(data[yr]['themes'].get(theme, 0))
        theme_matrix[theme] = list(zip(years, values))

    # Detect new themes (first appearance)
    first_appearance = {}
    for theme, vals in theme_matrix.items():
        for yr, count in vals:
            if count > 0 and theme not in first_appearance:
                first_appearance[theme] = yr
                break

    lines.append("THEME EVOLUTION (mentions per year)")
    lines.append("-" * 60)
    for theme, vals in theme_matrix.items():
        if any(c > 0 for _, c in vals):
            marker = f" [new FY{first_appearance.get(theme, '')}]" if theme in first_appearance else ""
            counts_str = ' '.join(f'{yr}:{c}' for yr, c in vals if c > 0)
            lines.append(f"  {theme:20s} {counts_str}{marker}")
    lines.append("")

    # --- SENTIMENT TREND ---
    pos_vals = [data[y]['sentiment'][0] for y in years]
    neg_vals = [data[y]['sentiment'][1] for y in years]

    lines.append("SENTIMENT")
    lines.append("-" * 40)
    lines.append(f"  Positive markers:  {' '.join(f'{y}:{v}' for y, v in zip(years, pos_vals))}")
    lines.append(f"  Negative markers:  {' '.join(f'{y}:{v}' for y, v in zip(years, neg_vals))}")

    # Trend
    if pos_vals[-1] > pos_vals[0] * 1.3:
        pos_trend = "rising confidence"
    elif pos_vals[-1] < pos_vals[0] * 0.7:
        pos_trend = "declining optimism"
    else:
        pos_trend = "stable"

    if neg_vals[-1] > neg_vals[0] * 1.3:
        neg_trend = "rising anxiety"
    elif neg_vals[-1] < neg_vals[0] * 0.7:
        neg_trend = "declining risk language"
    else:
        neg_trend = "stable"

    lines.append(f"  → {pos_trend}, {neg_trend}")
    lines.append("")

    # --- RISK CATEGORY EVOLUTION ---
    lines.append("RISK CATEGORIES (top 5 per year)")
    lines.append("-" * 40)
    for yr in years:
        risks = data[yr]['risk']
        top = sorted(risks.items(), key=lambda x: -x[1])[:5]
        top_str = ', '.join(f'{k}({v})' for k, v in top if v > 0)
        lines.append(f"  FY{yr}: {top_str}")

    # New risk categories
    new_risks = set()
    for yr in years:
        for cat, count in data[yr]['risk'].items():
            if count > 0:
                if not any(data[y]['risk'].get(cat, 0) > 0 for y in years if y < yr):
                    new_risks.add((cat, yr))

    if new_risks:
        lines.append(f"  New risk dimensions: {', '.join(f'{cat}(FY{yr})' for cat, yr in new_risks)}")
    lines.append("")

    # --- CONFIDENCE SIGNALS ---
    lines.append("CONFIDENCE SIGNALS")
    lines.append("-" * 40)
    for yr in years:
        conf = data[yr]['confidence']
        certain_count = len(conf['certain'])
        hedged_count = len(conf['hedged'])
        maturity = len(conf['product_maturity'])

        certainty_ratio = certain_count / max(hedged_count, 1)
        tone = "confident" if certainty_ratio > 2 else "cautious" if certainty_ratio < 0.5 else "balanced"

        lines.append(f"  FY{yr}: {tone} (certain:{certain_count} / hedged:{hedged_count}, ratio:{certainty_ratio:.1f})")

        # Show 1 example each
        if conf['certain'] and len(conf['certain']) <= 3:
            lines.append(f"    → {conf['certain'][0][:150]}")
        if conf['hedged'] and len(conf['hedged']) <= 3:
            lines.append(f"    → {conf['hedged'][0][:150]}")
        if conf['product_maturity']:
            lines.append(f"    [maturity signal: {conf['product_maturity'][0][:150]}]")
    lines.append("")

    # --- PRODUCT NARRATIVE ---
    lines.append("PRODUCT MENTION TREND")
    lines.append("-" * 40)
    product_themes = {'ai', 'spatial', 'services'}
    for theme in product_themes:
        if any(data[y]['themes'].get(theme, 0) > 0 for y in years):
            counts = [data[y]['themes'].get(theme, 0) for y in years]
            lines.append(f"  {theme:20s} {' '.join(f'{y}:{c}' for y, c in zip(years, counts))}")
    lines.append("")

    # --- KEY RISK QUOTES ---
    lines.append("RISK TONE EXCERPTS")
    lines.append("-" * 40)
    for yr in years:
        risk_text = data[yr]['sections'].get('risk', '')
        # Find sentences with "materially" or "may materially"
        if risk_text:
            sentences = re.split(r'(?<=[.!?])\s+', risk_text)
            key_sentences = [s for s in sentences if 'materially' in s.lower() or 'material adverse' in s.lower()]
            if key_sentences:
                # Show the most severe one
                most_severe = max(key_sentences, key=lambda s: len(re.findall(r'materially|adverse|risk', s, re.IGNORECASE)))
                lines.append(f"  FY{yr}: {most_severe[:200]}...")
    lines.append("")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Analyse annual report trends')
    parser.add_argument('--symbol', required=True, help='Company ticker (e.g., AAPL)')
    parser.add_argument('--base-dir', default='data/raw/snp/annual_reports',
                        help='Base directory for annual reports')
    parser.add_argument('--years', type=int, default=5, help='Number of years to analyse')
    parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    args = parser.parse_args()

    result = analyse_company(args.symbol, args.base_dir, args.years)
    if result is None:
        sys.exit(1)

    symbol, years, data = result
    report = format_report(symbol, years, data)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == '__main__':
    main()
