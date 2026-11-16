from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from pypdf import PdfReader

MAX_BYTES = 8 * 1024 * 1024


def extract_resume(content: bytes, filename: str) -> tuple[str, list[str]]:
    suffix = Path(filename).suffix.lower()
    warnings = []
    if len(content) > MAX_BYTES:
        raise ValueError('简历请控制在 8 MB 以内')
    if suffix in ('.txt', '.md'):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = content.decode('gb18030')
            except UnicodeDecodeError as exc:
                raise ValueError('文本编码无法识别，请另存为 UTF-8 TXT 后上传') from exc
    elif suffix == '.pdf':
        if not content.startswith(b'%PDF'):
            raise ValueError('文件内容不是有效的 PDF')
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise ValueError('暂不支持加密 PDF，请解密后再上传')
        if len(reader.pages) > 30:
            raise ValueError('最小版一次最多处理 30 页简历')
        text = '\n'.join((p.extract_text() or '') for p in reader.pages)
    elif suffix == '.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(x.file_size for x in entries) > 30 * 1024 * 1024:
                    raise ValueError('DOCX 解压后过大，请导出为 PDF 或 TXT')
                info = archive.getinfo('word/document.xml')
                if info.file_size > 4 * 1024 * 1024:
                    raise ValueError('DOCX 正文过大，请精简后重试')
                root = ET.fromstring(archive.read(info))
                text = '\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ValueError('DOCX 文件无法解析，请确认文件未损坏') from exc
    else:
        raise ValueError('支持 PDF、DOCX、TXT 和 Markdown；旧版 DOC 请先转换')
    text = text.replace('\x00', '').strip()
    if len(text) < 15:
        raise ValueError('未提取到足够文字。扫描版 PDF 暂无 OCR，请粘贴文字或上传可复制文字的 PDF')
    if len(text) > 60000:
        warnings.append('正文超过最小版长度上限，已保留前 60000 字符')
    warnings.append('只在本机保存提取文字，原始上传文件不落盘；请在画像中核对识别结果')
    return text[:60000], warnings
