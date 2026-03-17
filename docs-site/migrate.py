#!/usr/bin/env python3
"""
Migrate docs/ → docs-site/src/content/docs/

Transforms each markdown file:
  - Strips the hand-written "## On this page" TOC (Starlight auto-generates it)
  - Strips the ← prev | next → footer nav (Starlight handles this via sidebar)
  - Updates internal links: removes .md extension  (concepts.md → /concepts/)
  - Upgrades blockquote callouts to Starlight admonitions
  - Keeps YAML frontmatter as-is (Starlight uses title + description)
"""

import re
import shutil
from pathlib import Path

SRC = Path(__file__).parent.parent / "docs"
DST = Path(__file__).parent / "src/content/docs"

CALLOUT_MAP = {
    "**Warning:**": ":::caution",
    "**warning:**": ":::caution",
    "**Note:**": ":::note",
    "**note:**": ":::note",
    "**Tip:**": ":::tip",
    "**tip:**": ":::tip",
}

# Map old .md links to Starlight slug paths
LINK_RE = re.compile(r'\[([^\]]+)\]\(([a-z][a-z0-9_-]+)\.md\)')

def upgrade_callouts(text: str) -> str:
    """Convert > **Note:** ... blockquotes to Starlight :::note admonitions."""
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('> '):
            # Collect all consecutive blockquote lines
            block = []
            while i < len(lines) and lines[i].startswith('> '):
                block.append(lines[i][2:])  # strip '> '
                i += 1
            content = '\n'.join(block)
            # Check if it starts with a known callout marker
            matched = False
            for marker, directive in CALLOUT_MAP.items():
                if content.startswith(marker):
                    inner = content[len(marker):].strip()
                    label = directive.split(':::')[1].capitalize()
                    out.append(f'{directive}[{label}]')
                    out.append(inner)
                    out.append(':::')
                    matched = True
                    break
            if not matched:
                # Restore as plain blockquote
                for bl in block:
                    out.append(f'> {bl}')
        else:
            out.append(line)
            i += 1
    return '\n'.join(out)

def strip_on_this_page(text: str) -> str:
    """Remove the hand-written ## On this page section."""
    return re.sub(
        r'\n## On this page\n\n(?:- .+\n)+',
        '\n',
        text,
    )

def strip_prev_next_nav(text: str) -> str:
    """Remove ← prev | next → footer lines."""
    return re.sub(
        r'\n---\n\n←.*?→.*?\n$',
        '\n',
        text,
        flags=re.MULTILINE,
    )

def update_links(text: str) -> str:
    """Convert relative .md links to Starlight slug paths."""
    def replace(m):
        label, slug = m.group(1), m.group(2)
        return f'[{label}](/{slug}/)'
    return LINK_RE.sub(replace, text)

def migrate_file(src_path: Path, dst_path: Path) -> None:
    text = src_path.read_text()
    text = strip_on_this_page(text)
    text = strip_prev_next_nav(text)
    text = update_links(text)
    text = upgrade_callouts(text)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(text)
    print(f'  ✓ {src_path.name}')

def main():
    DST.mkdir(parents=True, exist_ok=True)
    md_files = sorted(SRC.glob('*.md'))

    print(f'Migrating {len(md_files)} files from docs/ → docs-site/src/content/docs/')
    for src in md_files:
        migrate_file(src, DST / src.name)

    # Copy slack-manifest.yaml to public/
    manifest_src = SRC / 'slack-manifest.yaml'
    manifest_dst = Path(__file__).parent / 'public/slack-manifest.yaml'
    if manifest_src.exists():
        shutil.copy(manifest_src, manifest_dst)
        print(f'  ✓ slack-manifest.yaml → public/')

    print('\nDone. Review docs-site/src/content/docs/ then run:')
    print('  cd docs-site && npm install && npm run dev')

if __name__ == '__main__':
    main()
