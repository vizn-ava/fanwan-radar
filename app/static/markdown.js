"use strict";

(() => {
  const cache = new WeakMap();
  const str = (value) => typeof value === "string" ? value : "";
  const element = (tag, content) => { const el = document.createElement(tag); if (content !== undefined) el.textContent = content; return el; };
  const entities = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: "\u00a0" };

  function decodeText(value) {
    return str(value).replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (raw, key) => {
      if (key[0] !== "#") return entities[key.toLowerCase()] || raw;
      const code = /^#x/i.test(key) ? parseInt(key.slice(2), 16) : Number(key.slice(1));
      return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : "\ufffd";
    });
  }
  function safeLink(value) {
    const href = decodeText(value);
    if (/[\u0000-\u001f\u007f]/.test(href)) return null;
    try { const url = new URL(href); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  function plainInline(tokens, fallback = "", depth = 0) {
    if (!Array.isArray(tokens) || depth > 48) return str(fallback);
    return tokens.map((token) => {
      if (token.type === "br") return "\n";
      if (token.type === "html" || token.type === "codespan") return str(token.text || token.raw);
      return token.tokens ? plainInline(token.tokens, token.text, depth + 1) : decodeText(token.text || token.raw);
    }).join("");
  }
  function appendFallback(parent, token) { parent.append(document.createTextNode(str(token?.raw || token?.text))); }
  function taskCheckbox(checked) {
    const checkbox = element("input"); checkbox.type = "checkbox"; checkbox.disabled = true; checkbox.checked = checked === true;
    checkbox.setAttribute("aria-label", checkbox.checked ? "已完成" : "未完成"); return checkbox;
  }

  function inline(parent, tokens, context, depth = 0) {
    if (!Array.isArray(tokens)) return;
    for (const token of tokens) {
      if (!token || typeof token !== "object") continue;
      if (depth > 48 || --context.remaining <= 0) { appendFallback(parent, token); continue; }
      switch (token.type) {
        case "strong": case "em": case "del": {
          const el = element(token.type);
          if (Array.isArray(token.tokens)) inline(el, token.tokens, context, depth + 1); else el.textContent = decodeText(token.text);
          parent.append(el); break;
        }
        case "codespan": parent.append(element("code", str(token.text))); break;
        case "checkbox": parent.append(taskCheckbox(token.checked)); break;
        case "br": parent.append(element("br")); break;
        case "link": {
          const href = safeLink(token.href), el = element(href ? "a" : "span");
          if (href) { el.href = href; el.target = "_blank"; el.rel = "noopener noreferrer"; el.referrerPolicy = "no-referrer"; if (token.title) el.title = decodeText(token.title); }
          else { el.className = "markdown-link-blocked"; el.title = "不支持的链接，仅显示文字"; }
          if (Array.isArray(token.tokens)) inline(el, token.tokens, context, depth + 1); else el.textContent = decodeText(token.text);
          parent.append(el); break;
        }
        case "image": {
          const alt = plainInline(token.tokens, decodeText(token.text));
          const el = element("span", `[图片未加载${alt ? `：${alt}` : ""}]`); el.className = "markdown-image-alt"; parent.append(el); break;
        }
        case "html": parent.append(document.createTextNode(str(token.raw || token.text))); break;
        case "escape": parent.append(document.createTextNode(str(token.text))); break;
        case "text":
          if (Array.isArray(token.tokens)) inline(parent, token.tokens, context, depth + 1); else parent.append(document.createTextNode(decodeText(token.text || token.raw)));
          break;
        default: appendFallback(parent, token);
      }
    }
  }
  function inlineBody(parent, token, context, depth) {
    if (Array.isArray(token.tokens)) inline(parent, token.tokens, context, depth + 1); else parent.textContent = decodeText(token.text || token.raw);
  }
  function tableCell(cell, tag, align, context, depth) {
    const el = element(tag);
    if (["left", "center", "right"].includes(align)) el.style.textAlign = align;
    if (tag === "th") el.scope = "col";
    if (cell && typeof cell === "object") inlineBody(el, cell, context, depth); else el.textContent = decodeText(cell);
    return el;
  }
  function block(token, context, depth = 0) {
    if (depth > 48 || --context.remaining <= 0) return element("p", str(token.raw || token.text));
    switch (token.type) {
      case "space": case "def": return null;
      case "heading": {
        const level = Math.max(1, Math.min(6, Math.trunc(Number(token.depth)) || 1)), el = element(`h${level}`);
        inlineBody(el, token, context, depth); return el;
      }
      case "paragraph": case "text": {
        const el = element("p"); inlineBody(el, token, context, depth); return el;
      }
      case "hr": return element("hr");
      case "checkbox": return taskCheckbox(token.checked);
      case "code": {
        const el = element("pre"); el.append(element("code", str(token.text))); return el;
      }
      case "blockquote": {
        const el = element("blockquote"); blocks(el, token.tokens, context, depth + 1); return el;
      }
      case "list": {
        const el = element(token.ordered ? "ol" : "ul");
        if (token.ordered && Number.isInteger(Number(token.start)) && Number(token.start) > 0 && Number(token.start) < 1000000000) el.start = Number(token.start);
        for (const item of Array.isArray(token.items) ? token.items : []) {
          const li = element("li");
          if (Array.isArray(item.tokens)) {
            // Marked emits checkbox tokens itself, directly for tight lists or
            // inside paragraph tokens for loose lists. Do not prepend a second one.
            for (const child of item.tokens) {
              if (!item.loose && (child.type === "text" || child.type === "paragraph")) inlineBody(li, child, context, depth + 1);
              else { const rendered = block(child, context, depth + 1); if (rendered) li.append(rendered); }
            }
          } else {
            if (item.task) li.append(taskCheckbox(item.checked));
            li.append(document.createTextNode(decodeText(item.text)));
          }
          el.append(li);
        }
        return el;
      }
      case "table": {
        const wrap = element("div"), table = element("table"), head = element("thead"), header = element("tr"), body = element("tbody");
        wrap.className = "markdown-table-scroll"; wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "分析表格，可横向滚动查看");
        const align = Array.isArray(token.align) ? token.align : [];
        (Array.isArray(token.header) ? token.header : []).forEach((cell, i) => header.append(tableCell(cell, "th", align[i], context, depth)));
        head.append(header); table.append(head);
        for (const row of Array.isArray(token.rows) ? token.rows : []) { const tr = element("tr"); for (const [i, cell] of (Array.isArray(row) ? row : []).entries()) tr.append(tableCell(cell, "td", align[i], context, depth)); body.append(tr); }
        table.append(body); wrap.append(table); return wrap;
      }
      case "html": { const el = element("p", str(token.raw || token.text)); el.className = "markdown-literal-html"; return el; }
      default: return element("p", str(token.raw || token.text));
    }
  }
  function blocks(parent, tokens, context, depth = 0) {
    for (const token of Array.isArray(tokens) ? tokens : []) {
      if (!token || typeof token !== "object") continue;
      const el = block(token, context, depth); if (el) parent.append(el);
    }
  }
  function reset(container) { cache.delete(container); }
  function render(container, content) {
    if (!container || typeof container.replaceChildren !== "function") return container;
    const source = str(content), lexer = window.marked?.lexer;
    let tokens = null;
    if (typeof lexer === "function") { try { tokens = lexer(source, { gfm: true, breaks: false, pedantic: false }); } catch { /* Incomplete or unsupported input stays safe plain text. */ } }
    const mode = Array.isArray(tokens) ? "marked" : "plain", previous = cache.get(container);
    container.classList.add("markdown-body"); container.dataset.markdownMode = mode;
    if (previous?.source === source && previous.mode === mode && container.childNodes.length === previous.records.length && previous.records.every((entry, i) => container.childNodes[i] === entry.el)) return container;
    if (mode === "plain") { const el = element("p", source); container.replaceChildren(el); cache.set(container, { source, mode, records: [{ el }] }); return container; }
    const old = previous?.mode === "marked" ? previous.records : [], next = [], context = { remaining: 30000 };
    if (!old.length) container.replaceChildren();
    for (const token of tokens) {
      if (!token || ["space", "def"].includes(token.type)) continue;
      const signature = JSON.stringify(token), index = next.length;
      let entry = old[index];
      if (!entry || entry.signature !== signature || entry.el.parentNode !== container) {
        const el = block(token, context); if (!el) continue;
        if (entry?.el.parentNode === container) entry.el.replaceWith(el); else container.append(el);
        entry = { signature, el };
      }
      next.push(entry);
    }
    old.slice(next.length).forEach((entry) => entry.el.remove());
    const allowed = new Set(next.map((entry) => entry.el));
    Array.from(container.childNodes).forEach((child) => { if (!allowed.has(child)) child.remove(); });
    let cursor = container.firstChild;
    next.forEach((entry) => { if (entry.el !== cursor) container.insertBefore(entry.el, cursor); else cursor = cursor.nextSibling; });
    cache.set(container, { source, mode, records: next }); return container;
  }

  window.FanwanMarkdown = Object.freeze({ render, reset });
})();
