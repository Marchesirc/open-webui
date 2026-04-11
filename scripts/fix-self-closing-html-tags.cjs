const fs = require('fs');
const path = require('path');

const root = path.join(process.cwd(), 'src');
const tags = [
  'div',
  'textarea',
  'option',
  'pre',
  'button',
  'span',
  'slot',
  'iframe',
  'canvas',
  'section',
  'article',
  'header',
  'footer',
  'main',
  'nav',
  'label',
  'select',
  'datalist',
  'ul',
  'ol',
  'li',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
  'form',
  'dialog',
  'details',
  'summary',
  'p'
];

const re = new RegExp(`<(${tags.join('|')})\\b([^<>]*?)\\/>`, 'g');
const protectedBlockRe = /<(script|style)\b[\s\S]*?<\/\1>/gi;

let updatedFiles = 0;
let totalReplacements = 0;

function walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const filePath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      walk(filePath);
      continue;
    }

    if (!entry.isFile() || !filePath.endsWith('.svelte')) {
      continue;
    }

    const content = fs.readFileSync(filePath, 'utf8');
    let replacements = 0;
    let output = '';
    let lastIndex = 0;
    let match;

    while ((match = protectedBlockRe.exec(content)) !== null) {
      const before = content.slice(lastIndex, match.index);
      output += before.replace(re, (_, tagName, attrs) => {
        replacements += 1;
        return `<${tagName}${attrs}></${tagName}>`;
      });

      output += match[0];
      lastIndex = protectedBlockRe.lastIndex;
    }

    const tail = content.slice(lastIndex);
    output += tail.replace(re, (_, tagName, attrs) => {
      replacements += 1;
      return `<${tagName}${attrs}></${tagName}>`;
    });

    if (replacements > 0) {
      fs.writeFileSync(filePath, output);
      updatedFiles += 1;
      totalReplacements += replacements;
    }
  }
}

walk(root);
console.log(`UPDATED_FILES=${updatedFiles}`);
console.log(`TOTAL_REPLACEMENTS=${totalReplacements}`);
