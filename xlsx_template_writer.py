"""Write calculated tables into approved Excel OOXML template without Excel/Office.
Only source analysis tables enter the report; original template cell values are discarded.
The template provides approved worksheet structure, column layout and style.
"""
from __future__ import annotations
import copy,os,tempfile,zipfile,re,xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

MAIN='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DOCREL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKGREL='http://schemas.openxmlformats.org/package/2006/relationships'
NS={'x':MAIN,'r':DOCREL}

def colref(n):
    s=''
    while n:
        n,x=divmod(n-1,26);s=chr(65+x)+s
    return s

def sheet_paths(inp):
    w=ET.fromstring(inp.read('xl/workbook.xml'))
    rel=ET.fromstring(inp.read('xl/_rels/workbook.xml.rels'))
    targets={x.get('Id'):x.get('Target') for x in rel}
    pairs=[]
    for sheet in w.find('x:sheets',NS):
        name=sheet.get('name');tid=targets[sheet.get('{%s}id'%DOCREL)]
        path=tid.lstrip('/') if tid.startswith('/') else 'xl/'+tid
        pairs.append((name,path.replace('xl/xl/','xl/')))
    return pairs

def as_cell(val,ref,style=''):
    sty=f' s="{style}"' if style else ''
    if val is None:return f'<c r="{ref}"{sty}/>'
    if isinstance(val,bool):return f'<c r="{ref}"{sty} t="b"><v>{int(val)}</v></c>'
    if isinstance(val,(int,float)):
        import math
        if math.isfinite(val):return f'<c r="{ref}"{sty}><v>{val}</v></c>'
    v=escape(str(val),{'"':'&quot;'})
    return f'<c r="{ref}"{sty} t="inlineStr"><is><t xml:space="preserve">{v}</t></is></c>'

def replace_sheet(original, rows):
    # Use original formatting and sheet metadata, replacing all data and formulas.
    # ET is used for metadata; streaming row strings avoid giant tree allocations.
    root=ET.fromstring(original)
    sheetdata=root.find('{%s}sheetData'%MAIN)
    if sheetdata is None:raise ValueError('sheetData missing')
    sample_styles={}
    for row in list(sheetdata)[1:12]:
        for cell in row.findall('{%s}c'%MAIN):
            ident=cell.get('r',''); letter=''.join(x for x in ident if x.isalpha())
            if letter and letter not in sample_styles:sample_styles[letter]=cell.get('s','')
    header_style='';data_style=''
    orig=list(sheetdata)
    if orig:
        first=orig[0].find('{%s}c'%MAIN)
        if first is not None:header_style=first.get('s','')
    if len(orig)>1:
        sec=orig[1].find('{%s}c'%MAIN)
        if sec is not None:data_style=sec.get('s','')
    # Rebuild worksheet XML with sheetData replaced. Preserve formatting, freezes,
    # column sizes and relevant dimensions. Avoid orphaned old rows.
    p=original.find(b'<sheetData')
    if p<0:
        p=original.find(b':sheetData')
    # Namespace prefixes may differ; regex tolerates both.
    import re
    pat=re.compile(rb'<(?:[A-Za-z0-9_]+:)?sheetData(?:\s[^>]*)?>.*?</(?:[A-Za-z0-9_]+:)?sheetData>',re.S)
    if not pat.search(original):raise ValueError('Cannot locate sheetData XML')
    parts=[b'<sheetData xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    for i,values in enumerate(rows,1):
        cells=[]
        for j,val in enumerate(values,1):
            ref=colref(j)+str(i)
            style=(header_style if i==1 else sample_styles.get(colref(j),data_style))
            cells.append(as_cell(val,ref,style))
        parts.append(('<row r="%d">'%i+''.join(cells)+'</row>').encode('utf8'))
    parts.append(b'</sheetData>')
    doc=pat.sub(lambda m:b''.join(parts),original,count=1)
    # Adjust dimension if supplied by template.
    maxcol=max((len(row) for row in rows),default=1)
    dimension=(f'A1:{colref(maxcol)}{max(1,len(rows))}').encode('ascii')
    doc=re.sub(rb'(<dimension\s+ref=")[^"]+("\s*/>)',lambda m:m.group(1)+dimension+m.group(2),doc,count=1)
    return doc

def write(template_path,output_path,rows_by_sheet,headers):
    source=Path(template_path);dest=Path(output_path)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source) as inp:
        mapping=dict(sheet_paths(inp))
        if set(headers)!=set(mapping):raise ValueError('Template sheet mismatch: '+str(set(headers)^set(mapping)))
        rev={v:k for k,v in mapping.items()}
        with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as out:
            for item in inp.infolist():
                raw=inp.read(item.filename)
                if item.filename=='xl/sharedStrings.xml':
                    # Original report text must not persist as hidden source data.
                    raw=(b'<?xml version="1.0" encoding="utf-8"?>'
                         b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="0" uniqueCount="0"/>')
                if item.filename in rev:
                    name=rev[item.filename]
                    raw=replace_sheet(raw,[headers[name]]+rows_by_sheet.get(name,[]))
                if item.filename=='xl/workbook.xml':
                    # Force internal 00_ sheet veryHidden, open visible station summary.
                    raw=re.sub(r'(<sheet\s+name="00_整體品質總覽"[^>]*?)(\s*/>)'.encode('utf8'),lambda m:re.sub(rb'\sstate="[^"]*"',b'',m.group(1))+b' state="veryHidden"'+m.group(2),raw,count=1)
                    raw=re.sub(rb'(<workbookView\b[^>]*\bactiveTab=")[^"]+',rb'\g<1>1',raw,count=1)
                out.writestr(item,raw)
    with zipfile.ZipFile(dest) as test:
        if test.testzip() is not None:raise ValueError('Output Excel CRC invalid')
        if len(sheet_paths(test))!=32:raise ValueError('Report sheet count !=32')
    return dest
