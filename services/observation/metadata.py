"""Bounded, non-executing metadata readers. Samples never imply full coverage."""
import csv
import io
import os
import json
from pathlib import PurePosixPath

SAMPLE_BYTES = 32768
FOOTER_BYTES = 262144

def _delimited_sample(sample, size, content, delimiter):
    """Read a bounded header and rows without treating a prefix as complete."""
    # Keep undecodable bytes as surrogate code points while parsing so an
    # invalid header cannot silently become a different valid schema. Body
    # rows are normalized below to preserve their JSON-encodable sample form.
    text=sample.decode('utf-8',errors='surrogateescape')
    if not text:
        return ([], [], 0, True) if size==0 else ([], [], None, False)
    stream=io.StringIO(text,newline='')
    reader=csv.reader(stream,delimiter=delimiter,strict=True)
    try:
        header=next(reader)
    except StopIteration:
        return [], [], 0, size==0
    except csv.Error:
        return [], [], None, False
    consumed=stream.tell()
    sample_complete=content is not None or len(sample)>=size
    # csv.reader consumes an open quoted record through EOF. A newline before
    # that EOF, or a full file read, proves that the first record is complete.
    header_complete=sample_complete or consumed<len(text) or text[:consumed].endswith(('\n','\r'))
    if not header_complete:
        return [], [], None, False
    if any('\udc80' <= char <= '\udcff' for value in header for char in value):
        return [], [], None, False
    rows=[]
    row_stream=io.StringIO(text[consumed:].encode('utf-8',errors='surrogateescape').decode('utf-8',errors='ignore'), newline='')
    row_reader=csv.reader(row_stream,delimiter=delimiter,strict=True)
    try:
        for row in row_reader:
            rows.append([v[:240] for v in row[:100]])
            if len(rows)==10: break
    except csv.Error:
        pass
    return header, rows, len(header), True

def inspect(fd, size, path, content):
    suffix=PurePosixPath(path).suffix.lower()
    result={'size_bytes':size, 'content_complete':content is not None}
    def read_at(offset,count):
        return os.pread(fd,min(count,size-offset),offset)
    try:
        if suffix in ('.csv','.tsv'):
            sample=content if content is not None else read_at(0,SAMPLE_BYTES)
            header, rows, total_columns, header_complete=_delimited_sample(
                sample,size,content,'\t' if suffix=='.tsv' else ',')
            result.update(format='delimited',columns=[v[:240] for v in header[:100]],sample_rows=rows,
                          sample_bytes=len(sample),sample_only=True,total_rows=None,
                          total_columns=total_columns,
                          columns_truncated=not header_complete or len(header)>100)
        elif suffix=='.parquet':
            tail=read_at(max(0,size-8),8)
            if len(tail)!=8 or tail[4:]!=b'PAR1' or read_at(0,4)!=b'PAR1': raise ValueError()
            length=int.from_bytes(tail[:4],'little')
            if length>FOOTER_BYTES or length>size-12:
                result.update(format='parquet',coverage='footer exceeds metadata bound'); return result
            footer=read_at(size-8-length,length)
            import pyarrow as pa
            import pyarrow.parquet as pq
            meta=pq.ParquetFile(pa.BufferReader(b'PAR1'+footer+tail),thrift_string_size_limit=FOOTER_BYTES,
                                thrift_container_size_limit=10000,arrow_extensions_enabled=False).metadata
            result.update(format='parquet',rows=meta.num_rows,row_groups=meta.num_row_groups,
                          columns=[{'name':meta.schema.column(i).name[:240],'physical_type':meta.schema.column(i).physical_type}
                                   for i in range(min(meta.num_columns,100))],
                          columns_truncated=meta.num_columns>100,sample_rows=[],coverage='footer metadata only; no rows read')
        elif suffix in ('.png','.jpg','.jpeg','.gif','.webp'):
            from PIL import Image
            # Header parsing only; no raster decode or load().
            with Image.open(io.BytesIO(read_at(0,FOOTER_BYTES))) as im:
                result.update(format=im.format,width=im.width,height=im.height,coverage='bounded image header only')
        elif suffix=='.pdf' and content is not None:
            from pypdf import PdfReader
            document=PdfReader(io.BytesIO(content))
            result.update(format='pdf',pages=len(document.pages),coverage='page metadata; no OCR or text extraction')
        elif suffix in ('.pt','.pth','.ckpt','.pkl','.pickle','.bin'):
            result.update(format='binary',coverage='size and file identity only; not deserialized')
    except Exception:
        result.update(coverage='metadata unavailable or invalid; no content claims')
    for field in ('sample_rows','columns'):
        while result.get(field) and len(json.dumps(result,ensure_ascii=False).encode())>8192:
            result[field].pop(); result['metadata_fields_truncated']=True
            if field=='columns': result['columns_truncated']=True
    return result
