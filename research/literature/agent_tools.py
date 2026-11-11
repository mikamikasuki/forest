"""Native agents can import and read actual full-text publication evidence."""
from pathlib import Path
import re
import unicodedata
from sqlalchemy import select

from services.api.db import Session,SourcePaper,SourcePassage,asdict
from services.api.common import project_dir,emit
from research.publication.quality import _official
from .sources import import_identifier,download_pdf,extract_pdf,matches_publication
from .browser import read_page


def title_in_text(title,text):
    """Match PDF small caps/line spacing while retaining the title's characters."""
    compact=lambda value:re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',value).casefold())
    normalized=compact(title)
    return bool(normalized and normalized in compact(text))


def import_source(project_id,workspace,arguments,action_id):
    """Retain observed PDF/pages and final-proceedings provenance, not a claim."""
    root=project_dir(project_id)
    with Session() as session:
        for record in session.scalars(select(SourcePaper).where(SourcePaper.project_id==project_id)):
            if record.data.get('origin_action_id')==action_id:
                return {'source':asdict(record),'replayed':True,'exit_code':0}
    folder=root/'library'/('agent-'+action_id)
    folder.mkdir(parents=True,exist_ok=True)
    identifier=arguments['identifier']
    if not identifier.startswith(('http://','https://','10.')) and identifier.lower().endswith('.pdf'):
        path=(Path(workspace)/identifier).resolve()
        if not path.is_relative_to(Path(workspace).resolve()) or not path.is_file():
            raise ValueError('Local PDF imports must name an actual file inside the agent workspace')
        identifier=str(path)
    record=import_identifier(identifier,folder)
    pdf_url=arguments.get('pdf_url') or record.get('pdf_url')
    if record.get('read_scope')!='full_text' and pdf_url:
        pdf=download_pdf(pdf_url,folder/'fulltext.pdf')
        record={**record,'pdf_path':pdf,'passages':extract_pdf(pdf),'read_scope':'full_text'}
    acceptance=arguments.get('acceptance_url')
    if acceptance:
        attach_acceptance=True
        kind='official_decision' if 'openreview.net/forum' in acceptance else 'official_proceedings'
        if not _official(acceptance,kind):
            raise ValueError('Use the actual official proceedings/journal record or sourced OpenReview decision URL')
        page=read_page(acceptance,folder/'acceptance')
        if not _official(page['url'],kind):
            raise ValueError('Acceptance URL redirected away from an official final publication/decision record')
        title=arguments.get('title')
        if title:
            if not title_in_text(title,page['title']+' '+page['text']):
                raise ValueError('The supplied paper title does not match the actual official publication record')
            first_pages=' '.join(p.get('text','') for p in record.get('passages',[]) if isinstance(p.get('page'),int) and p['page']<=2)
            if record.get('read_scope')=='full_text' and not title_in_text(title,first_pages):
                raise ValueError('The supplied title does not match the actual imported full text; import the matching paper PDF')
        elif record.get('source') in ('local_pdf','public_pdf'):
            # Without an explicit title, only attach publication provenance when
            # the observed PDF title is present in both the official record and
            # the imported document itself. Otherwise preserve the PDF as an
            # independent source instead of pairing it with an unrelated paper.
            observed_title=record.get('title') or ''
            first_pages=' '.join(p.get('text','') for p in record.get('passages',[]) if isinstance(p.get('page'),int) and p['page']<=2)
            attach_acceptance=bool(
                page.get('title') and first_pages
                and title_in_text(observed_title,page['title']+' '+page.get('text',''))
                and title_in_text(observed_title,first_pages)
            )
        if attach_acceptance:
            if not title and record.get('source') in ('local_pdf','public_pdf'):
                record['title']=page['title']
            record={**record,'acceptance_url':page['url'],'acceptance_kind':kind,
                    'acceptance_read_scope':'observed_public_page',
                    'acceptance_title_match':bool(title) or bool(record.get('source') in ('local_pdf','public_pdf')),
                    'passages':[*record.get('passages',[]),{'id':'acceptance-record','page':None,'section':'Official publication/decision record','text':page['text'],'source_url':page['url']}],
                    'acceptance_text_path':str(Path(page['text_path']).relative_to(root))}
    if arguments.get('title'):
        record['title']=arguments['title']
    if record.get('pdf_path'):
        path=Path(record['pdf_path']).resolve()
        # A local input is copied into its ordinary editable library directory.
        if not path.is_relative_to(root.resolve()):
            import shutil
            destination=folder/'fulltext.pdf';shutil.copyfile(path,destination);path=destination.resolve()
        record['pdf_path']=str(path.relative_to(root.resolve()))
    record={**record,'origin_action_id':action_id,'trusted_instructions':False}
    with Session.begin() as session:
        source=next((candidate for candidate in session.scalars(
            select(SourcePaper).where(SourcePaper.project_id==project_id)
        ) if matches_publication(record,candidate.data,candidate.title)),None)
        if source is None:
            source=SourcePaper(project_id=project_id,title=record['title'],data=record,status='available');session.add(source);session.flush()
        else:
            source.data={**source.data,**record};source.revision+=1
        for passage in record.get('passages',[]):
            session.add(SourcePassage(project_id=project_id,title=source.title,data={'paper_id':source.id,**passage},status='available'))
        emit(session,project_id,'artifact_available',{'kind':'library','id':source.id})
        result=asdict(source)
    return {'source':result,'read_scope':record.get('read_scope'),'passage_count':len(record.get('passages',[])),
            'verification_scope':'Actual imported material and observed official page; a decision must be read before labeling the paper accepted.','exit_code':0}


def _citation_metadata(source):
    data=source.data or {}
    return {'authors':data.get('authors',[]),'year':data.get('year'),
            'doi':data.get('doi'),'journal':data.get('journal')}


def read_source(project_id,source_id=None,offset=0,limit=8):
    offset=max(0,int(offset));limit=max(1,min(30,int(limit)))
    with Session() as session:
        if source_id is None:
            rows=list(session.scalars(select(SourcePaper).where(SourcePaper.project_id==project_id).order_by(SourcePaper.created_at)))
            return {'sources':[{'id':row.id,'title':row.title,**_citation_metadata(row),
                                'read_scope':row.data.get('read_scope',row.data.get('reading_scope')),
                                'reading_scope':row.data.get('read_scope',row.data.get('reading_scope')),
                                'url':row.data.get('url'),'acceptance_url':row.data.get('acceptance_url'),
                                'passage_count':len(row.data.get('passages',[]))} for row in rows[offset:offset+limit]],
                    'total':len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None,'exit_code':0}
        source=session.get(SourcePaper,source_id)
        if source is None or source.project_id!=project_id:
            raise ValueError('Read a source ID from this project')
        passages=source.data.get('passages',[])
        reading_scope=source.data.get('read_scope',source.data.get('reading_scope'))
        return {'source_id':source.id,'title':source.title,**_citation_metadata(source),
                'read_scope':reading_scope,'reading_scope':reading_scope,'url':source.data.get('url'),
                'acceptance_url':source.data.get('acceptance_url'),'passages':passages[offset:offset+limit],
                'total':len(passages),'next_offset':offset+limit if offset+limit<len(passages) else None,
                'trusted_instructions':False,'exit_code':0}
