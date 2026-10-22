"""Portable XLSX/DOCX report export; standard OOXML, no Office or third-party packages."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as E
from zipfile import ZipFile, ZIP_DEFLATED
from common import METRICS, LABELS, UNITS, load, timezone

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


def xml(element):
    # OPC package readers require the package roots in their default namespace.
    namespace=element.tag.split('}')[0][1:]
    if namespace in (S,PKG,CT):E.register_namespace('',namespace)
    E.register_namespace('w',W);E.register_namespace('r',R)
    return E.tostring(element, encoding='utf-8', xml_declaration=True)
def el(ns, tag, attrs=None, text=None):
    x = E.Element('{'+ns+'}'+tag, attrs or {})
    if text is not None: x.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(text))
    return x
def child(parent, ns, tag, attrs=None, text=None):
    x = el(ns,tag,attrs,text);parent.append(x);return x
def col(n):
    s=''
    while n: n,v=divmod(n-1,26);s=chr(65+v)+s
    return s
def value(v, style=0, formula=None): return {'v':v,'s':style,'f':formula}
def shown(key,v):
    if v is None:return None
    return v/86400 if key=='seconds' else v/10000 if key in ('input_tokens','output_tokens') else v
def metric_style(key, mean=False):
    return 8 if key=='seconds' and mean else 7 if key=='seconds' else 6 if key=='cache_rate' else (5 if mean else 4) if key in ('input_tokens','output_tokens') else 10 if mean else 3
def fmt_duration(seconds, milliseconds=False):
    if seconds is None:return 'δȡ��'
    import math
    n=math.floor(seconds*(1000 if milliseconds else 1)+0.5);ss,ms=divmod(n,1000) if milliseconds else (n,0)
    h,rem=divmod(ss,3600);m,s=divmod(rem,60)
    return f'{h}ʱ{m:02d}��{s:02d}'+(f'.{ms:03d}' if milliseconds else '')+'��'
def text_metric(key,v,mean=False):
    if v is None:return 'δȡ��'
    if key=='seconds':return fmt_duration(v, not mean)
    if key=='cache_rate':return f'{v:.2%}'
    if key in ('input_tokens','output_tokens'):return f'{v/10000:,.6f}' if mean else f'{v/10000:,.4f}'
    return f'{v:.2f}' if mean else str(v)
def excel_timestamp(ms,tz):
    if ms is None:return None
    import datetime as dt
    local=dt.datetime.fromtimestamp(ms/1000,tz).replace(tzinfo=None)
    return (local-dt.datetime(1899,12,30)).total_seconds()/86400


def relationships(entries):
    root=el(PKG,'Relationships')
    for i,kind,target in entries:child(root,PKG,'Relationship',{'Id':i,'Type':R+'/'+kind,'Target':target})
    return xml(root)


def excel_styles():
    root=el(S,'styleSheet')
    fmts=['#,##0','#,##0.0000','#,##0.000000','0.00%','[h]"ʱ"mm"��"ss.000"��"','[h]"ʱ"mm"��"ss"��"','yyyy-mm-dd hh:mm:ss.000','0.00']
    nf=child(root,S,'numFmts',{'count':str(len(fmts))})
    for i,f in enumerate(fmts,164):child(nf,S,'numFmt',{'numFmtId':str(i),'formatCode':f})
    fonts=child(root,S,'fonts',{'count':'3'})
    for i in range(3):
        f=child(fonts,S,'font');child(f,S,'sz',{'val':'16' if i==2 else '10'});child(f,S,'name',{'val':'Microsoft YaHei'})
        if i:child(f,S,'b')
        child(f,S,'color',{'rgb':'FFFFFFFF' if i==1 else 'FF202A35'})
    fills=child(root,S,'fills',{'count':'3'})
    child(child(fills,S,'fill'),S,'patternFill',{'patternType':'none'})
    child(child(fills,S,'fill'),S,'patternFill',{'patternType':'gray125'})
    solid=child(child(fills,S,'fill'),S,'patternFill',{'patternType':'solid'});child(solid,S,'fgColor',{'rgb':'FF245B83'})
    border=child(child(root,S,'borders',{'count':'1'}),S,'border')
    for side in ('left','right','top','bottom','diagonal'):child(border,S,side)
    child(child(root,S,'cellStyleXfs',{'count':'1'}),S,'xf',{'numFmtId':'0','fontId':'0','fillId':'0','borderId':'0'})
    ids=[0,0,0,164,165,166,167,168,169,170,171,0]
    xfs=child(root,S,'cellXfs',{'count':str(len(ids))})
    for i,number_format in enumerate(ids):
        x=child(xfs,S,'xf',{'numFmtId':str(number_format),'fontId':'1' if i==1 else '2' if i==2 else '0',
                          'fillId':'2' if i==1 else '0','borderId':'0','xfId':'0','applyNumberFormat':'1','applyAlignment':'1'})
        child(x,S,'alignment',{'vertical':'center','wrapText':'1','horizontal':'center' if i==1 else 'right' if 3<=i<=10 else 'left'})
    child(child(root,S,'cellStyles',{'count':'1'}),S,'cellStyle',{'name':'Normal','xfId':'0','builtinId':'0'})
    return xml(root)


def worksheet(rows,widths,freeze=0,filter_row=None,merges=None):
    root=el(S,'worksheet');views=child(root,S,'sheetViews');view=child(views,S,'sheetView',{'workbookViewId':'0','showGridLines':'0'})
    if freeze:child(view,S,'pane',{'ySplit':str(freeze),'topLeftCell':f'A{freeze+1}','activePane':'bottomLeft','state':'frozen'})
    child(root,S,'sheetFormatPr',{'defaultRowHeight':'22'})
    cs=child(root,S,'cols')
    for i,w in enumerate(widths,1):child(cs,S,'col',{'min':str(i),'max':str(i),'width':str(w),'customWidth':'1'})
    sd=child(root,S,'sheetData')
    for ri,row in enumerate(rows,1):
        rr=child(sd,S,'row',{'r':str(ri),'ht':str(38 if ri==filter_row else 76 if freeze==1 and ri>1 else 26),'customHeight':'1'})
        for ci,item in enumerate(row,1):
            d=item if isinstance(item,dict) else value(item)
            if d['v'] is None and not d['f']:continue
            c=child(rr,S,'c',{'r':col(ci)+str(ri),'s':str(d['s'])});v=d['v']
            if d['f']:
                if not isinstance(v,(float,int)):c.set('t','str')
                child(c,S,'f',text=d['f']);child(c,S,'v',text=v if v is not None else 'δȡ��')
            elif isinstance(v,(float,int)) and not isinstance(v,bool):child(c,S,'v',text=repr(v))
            else:
                c.set('t','inlineStr');t=child(child(c,S,'is'),S,'t',text=v);t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    if filter_row:child(root,S,'autoFilter',{'ref':f'A{filter_row}:{col(len(widths))}{len(rows)}'})
    if merges:
        mc=child(root,S,'mergeCells',{'count':str(len(merges))})
        for ref in merges:child(mc,S,'mergeCell',{'ref':ref})
    child(root,S,'pageMargins',{'left':'0.3','right':'0.3','top':'0.5','bottom':'0.5','header':'0.2','footer':'0.2'})
    return xml(root)


def excel_report(data,path):
    runs=data['runs'];tasks=data['tasks'];tz=timezone(data['meta']['timezone'])
    headers=[f'{a} {b}' for a,b in zip(LABELS,UNITS)]
    raw=[list(map(lambda s:value(s,1),['����ID','���','��������','���״̬','��ʼʱ��','����ʱ��',*headers,'�����ȡToken ��','���ʱ ʱ����','�μ�ȴ� ʱ����','ִ�зֶ���','ģ�ͼ�¼','��������','�ỰID','�û���ϢID','��Դ�ļ�','����Ŀ¼','����·��','ҳ��״̬','ҳ��ظ�','ҳ������']))]
    segments=[list(map(lambda s:value(s,1),['����ID','���','ִ�ж�','��������','��ʼʱ��','����ʱ��',*headers,'�û���ϢID','˵��']))]
    for r in runs:
        observations=r.get('page_observations',[])
        raw.append([r['run_id'],r['task'],r['attempt'],r['status'],value(excel_timestamp(r['start_ms'],tz),9),value(excel_timestamp(r['end_ms'],tz),9),
                    *[value(shown(k,r['values'][k]),metric_style(k)) for k in METRICS],value(r['cache_tokens']/10000 if r['cache_tokens'] is not None else None,4),
                    value(shown('seconds',r['active_seconds']),7),value(shown('seconds',r['gap_seconds']),7),r['segment_count'],' / '.join(r['models']),r['conditions'],
                    r['session'],r['user_message'],r['source'],r['directory'],'\n'.join(f['path'] for f in r['files']),
                    '\n'.join(o.get('page_status','') for o in observations),'\n'.join(o.get('page_response','') for o in observations),'\n'.join(o['evidence'] for o in observations)])
        for s in r['segments']:
            segments.append([r['run_id'],r['task'],s['segment'],s['segment_kind'],value(excel_timestamp(s['start_ms'],tz),9),value(excel_timestamp(s['end_ms'],tz),9),
                             *[value(shown(k,s['values'][k]),metric_style(k)) for k in METRICS],s['user_message'],'��'.join(s['warnings'])])
    summary=[[value(data['product']+' �˲���־ͳ��',2)],[],['�����ֶκ�ʱ�������룬��ֵ��ʾ���룻Token����Ϊ��λ��'],[],[],[value(s,1) for s in ['���','��Ŀ','��������',*headers]]]
    limit=max(201,len(raw)+100); rawcols=[col(i) for i in range(7,16)]
    def rg(c):return f"'��μ�¼'!${c}$2:${c}${limit}"
    for row,t in enumerate(tasks,7):
        cells=[t['id'],t['name'],value(sum(r['task']==t['id'] for r in runs),3,f'COUNTIF({rg("B")},A{row})')]
        for i,k in enumerate(METRICS):
            rr=rg(rawcols[i]);n=f'COUNTIFS({rg("B")},A{row},{rr},"<>")'
            formula=f'IF({n}=0,"δȡ��",SUMIFS({rr},{rg("B")},A{row})/{n})'
            cells.append(value(shown(k,data['summary'][k]['by_task'][t['id']]['mean']),metric_style(k,True),formula))
        summary.append(cells)
    summary.append([]);lasttask=6+len(tasks)
    for which,label in [('task_mean','�����ֵ ��Ŀ��Ȩ'),('run_mean','ȫ�����Ծ�ֵ ����'),('task_n','��Ч��Ŀ��'),('run_n','��Ч���Դ���')]:
        cells=[label,None,len({r['task'] for r in runs}) if which=='task_mean' else len(runs) if which=='run_mean' else None]
        for i,k in enumerate(METRICS):
            dest=col(4+i);rr=rg(rawcols[i]);taskrange=f'{dest}7:{dest}{lasttask}'
            target=taskrange if which in ('task_mean','task_n') else rr
            formula=f'COUNT({target})' if which.endswith('_n') else f'IF(COUNT({target})=0,"δȡ��",AVERAGE({target}))'
            cells.append(value(data['summary'][k][which] if which.endswith('_n') else shown(k,data['summary'][k][which]),3 if which.endswith('_n') else metric_style(k,True),formula))
        summary.append(cells)
    summary.append([])
    paired_den=f'SUMIFS({rg("I")},{rg("P")},"<>")';paired_num=f'SUMIFS({rg("P")},{rg("I")},"<>")'
    summary.append(['���建�������� ��������Ȩ',None,None,None,None,None,None,value(data['pooled_cache_rate'],6,f'IF({paired_den}=0,"δȡ��",{paired_num}/{paired_den})')])
    for note in ['��Ҫ�����ֵ����ͬ����ξ�ֵ���ٰ���Ŀ��Ȩ�����ξ�ֵ���С�',
                 'ȱʧֵ�����㣻��������ֵ��ʧ��ִ�в���ͳ�ơ�ÿ���г���Ч��Ŀ���ʹ�����',
                 '���������ύ����һ�Σ�����ָ��Ϊԭ���Ժ����Σ��ڲ�ģ�����Բ����Ӷ������Դ�����',
                 'LLM��ɵ������������������������¼�ĵ��ã�ʧ���������ǡ�',
                 f'��ʽԤ����μ�¼����{limit}�У�����ʱ�������вɼ������ɱ��档']:
        summary.append([note])
    definitions=[['��ʱ','ʱ����','�û��ύ��������ģ�ͻظ����������Ժ��μ�ȴ����ֶα�����ʱ���С�'],
                 ['LLM��ɵ�����','��','���������������¼��LLM���ã�����Ϣ�������ʶȥ�أ�ʧ�����Ե��С�'],
                 ['����Token','��','�����ε����ۼӣ��������ȡ��д�룻ͬһ�������ڶ������ٴ������Ի������'],
                 ['���Token','��','��ȡ����ֶκ��ۼӣ�1�����10000 Token��'],
                 ['����������','%','�����ȡToken/�ۼ�����Token�����������������ֵΪ������������Ȩֵ���С�'],
                 ['���ߵ���','��','�����ߵ��ñ�ʶȥ�ؼ���������ʧ�ܵ��á�'],
                 ['���ߴ���','��','��ȷ����״̬���ڲ������ǻ���������˳��룬�����ñ�ʶȥ�ء�'],
                 ['��������','��','��ͳ�Ʊ�����ר���������ߵ��ã������������ã���ҳץȡ���������������й۲졣'],
                 ['�����ļ���','��','�ɹ��������ߵǼ��Ҵ��̴��ڵ�Ψһ·������δ�Ǽ��ļ����Զ����롣']]
    method=[[value('ָ��ھ�����Դ',2)],[],[value(x,1) for x in ['ָ��','��λ','�ھ�']],*definitions,[],
            ['���󷽷�',None,data['meta']['method']],['������Դ',None,data['meta']['source']],['�ɼ�ʱ��',None,data['meta']['collected_at']],
            ['ʱ��',None,data['meta']['timezone']],['���ζ�ȡһ��',None,str(data['meta']['read_twice_equal'])],
            ['���ݿ���ժҪ',None,data['meta']['snapshot_sha256']],['δƥ����Ŀ',None,'��'.join(data['missing_tasks']) or '��']]
    sheets=[('�����ֵ',summary,[33,32,12,23,22,21,21,18,18,18,18,18],6,None),
            ('��μ�¼',raw,[25,11,12,42,29,29,25,22,20,20,18,18,18,18,18,22,25,25,14,25,45,45,45,65,65,65,22,45,45],1,1),
            ('ִ�зֶ�',segments,[25,11,12,18,29,29,25,22,20,20,18,18,18,18,18,45,48],1,1),
            ('�ھ�����Դ',method,[27,14,115],0,None)]
    content=el(CT,'Types');child(content,CT,'Default',{'Extension':'rels','ContentType':'application/vnd.openxmlformats-package.relationships+xml'});child(content,CT,'Default',{'Extension':'xml','ContentType':'application/xml'})
    for part,ctype in [('workbook','sheet.main'),('styles','styles')]:
        child(content,CT,'Override',{'PartName':'/xl/'+part+'.xml','ContentType':'application/vnd.openxmlformats-officedocument.spreadsheetml.'+ctype+'+xml'})
    workbook=el(S,'workbook');sh=child(workbook,S,'sheets')
    rels=[]
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        for i,(name,rows,widths,freeze,autofilter) in enumerate(sheets,1):
            child(sh,S,'sheet',{'name':name,'sheetId':str(i),'{'+R+'}id':f'rId{i}'})
            child(content,CT,'Override',{'PartName':f'/xl/worksheets/sheet{i}.xml','ContentType':'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})
            rels.append((f'rId{i}','worksheet',f'worksheets/sheet{i}.xml'))
            merges=([f'A{j}:L{j}' for j,row in enumerate(rows,1) if row and len(row)==1] if name=='�����ֵ' else ['A1:C1'] if name=='�ھ�����Դ' else [])
            z.writestr(f'xl/worksheets/sheet{i}.xml',worksheet(rows,widths,freeze,autofilter,merges))
        child(workbook,S,'calcPr',{'calcId':'191029','fullCalcOnLoad':'1'})
        rels.append(('rStyles','styles','styles.xml'))
        z.writestr('[Content_Types].xml',xml(content));z.writestr('_rels/.rels',relationships([('rId1','officeDocument','xl/workbook.xml')]))
        z.writestr('xl/workbook.xml',xml(workbook));z.writestr('xl/_rels/workbook.xml.rels',relationships(rels));z.writestr('xl/styles.xml',excel_styles())


def clean(text):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(text)).replace('��','').replace('��','').replace('"','').replace('��','��')


def error_summary(error):
    text=error['message'];code=error.get('exit_code')
    patterns=[('SEARCH_TIME_RANGE_TOO_LONG','����ʱ�䷶Χ������������'),
              ('SEARCH_TIME_RANGE_UNSUPPORTED','�������߲�֧�����������ʷʱ������'),
              ('Unsupported browser URL protocol','�������֧�������е�URLЭ��'),
              ('A positional parameter cannot be found','�������λ�ô���'),
              ('file: command not found','file�������'),
              ('SyntaxError','�ű��﷨����'),
              ('Cannot display content of binary file','��ȡ���߲�֧��ֱ����ʾ�������ļ�'),
              ('File does not exist','Ŀ���ļ�������'),
              ('AssertionError','���ű�����ʧ��'),
              ('failed to read file','ͼ�񹤾߶�ȡ�ļ�ʧ��'),
              ('cannot find the path','������ļ�·��������')]
    http=re.search(r'status code:\s*(\d+)',text)
    if http:return '��ҳץȡ����HTTP '+http.group(1)
    if 'punycode' in text and code not in (None,0):return f'�����˳���{code}�������Node������ʾ���������һ���˲�'
    for pattern,summary in patterns:
        if pattern in text:return summary+(f'���˳���{code}' if code not in (None,0) else '')
    if 'timed out' in text or 'deadline exceeded' in text:return '��ҳץȡ��ʱ'
    return text[:160]+('��' if len(text)>160 else '')


def short_time(text):
    return text[:10]+'\n'+text[11:23] if text else 'ʱ��δ��¼'


def word_report(data,path,analysis=None):
    document=el(W,'document');body=child(document,W,'body')
    def paragraph(text,style='Normal',parent=body):
        pp=child(parent,W,'p');pr=child(pp,W,'pPr');child(pr,W,'pStyle',{'{'+W+'}val':style})
        for i,line in enumerate(clean(text).splitlines()):
            run=child(pp,W,'r')
            if i:child(run,W,'br')
            t=child(run,W,'t',text=line);t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        return pp
    def table(headers,rows,widths):
        t=child(body,W,'tbl');pr=child(t,W,'tblPr');child(pr,W,'tblW',{'{'+W+'}w':str(sum(widths)),'{'+W+'}type':'dxa'})
        child(pr,W,'tblLayout',{'{'+W+'}type':'fixed'})
        borders=child(pr,W,'tblBorders')
        for side in ('top','left','bottom','right','insideH','insideV'):child(borders,W,side,{'{'+W+'}val':'single','{'+W+'}sz':'4','{'+W+'}color':'D9D9D9'})
        mar=child(pr,W,'tblCellMar')
        for side in ('top','left','bottom','right'):child(mar,W,side,{'{'+W+'}w':'75','{'+W+'}type':'dxa'})
        grid=child(t,W,'tblGrid')
        for w in widths:child(grid,W,'gridCol',{'{'+W+'}w':str(w)})
        for ri,row in enumerate([headers]+rows):
            tr=child(t,W,'tr');trp=child(tr,W,'trPr')
            child(trp,W,'cantSplit')
            if ri==0:child(trp,W,'tblHeader')
            for ci,text in enumerate(row):
                cell=child(tr,W,'tc');p=child(cell,W,'tcPr');child(p,W,'tcW',{'{'+W+'}w':str(widths[ci]),'{'+W+'}type':'dxa'})
                child(p,W,'vAlign',{'{'+W+'}val':'center'})
                child(p,W,'shd',{'{'+W+'}fill':'E8EDF2' if ri==0 else 'F6F8FA' if ri%2==0 else 'FFFFFF'})
                paragraph(text,'TableHeader' if ri==0 else 'TableText',cell)
        paragraph('')
    paragraph(data['product']+' �˲���־�۲���ɼ�����','Title')
    paragraph('�ɼ�ʱ�� '+data['meta']['collected_at']+'    ʱ�� '+data['meta']['timezone'])
    paragraph(f"�����ҵ�{len(data['runs'])}�ζ������ԣ�{sum(r['segment_count'] for r in data['runs'])}��ִ�жΡ�ÿ��ִ�еľ���ָ�ꡢ��Ŀ��ֵ�Ͷ����ֵ������ͬ��Excel��")
    paragraph('��ɼ�¼�뽻���ļ��Ĵ����Էֱ���顣ҳ��״̬��ʵ�ʻظ���Ϊ����֤�ݱ����������¼ִ�й��̼��������⣬����������������Ӧ���������жϡ�')
    paragraph('��������������ֵ','Heading1')
    models=sorted({m for r in data['runs'] for m in r['models']})
    paragraph('��־�е�ģ�ͼ�¼��'+('��'.join(models) or 'δ��¼')+'��Ӧ�ð汾û�пɿ��ֶ�ʱ����Ϊδ��¼��')
    table(['ָ��','�����ֵ','��Ч��Ŀ��','��Ч����'],[[a+' '+u,text_metric(k,data['summary'][k]['task_mean'],True),data['summary'][k]['task_n'],data['summary'][k]['run_n']] for k,a,u in zip(METRICS,LABELS,UNITS)],[2400,3000,1800,1800])
    paragraph('������Ҫ��ֵ����ͬ�������Ч��ֵ�ľ�ֵ���ٰ���Ŀ��Ȩƽ����ȱʧֵ�����㣬��־������ʧ��ִ���Բ���ͳ�ơ�LLM��ɵ�����ֻ�����������������¼�ĵ��ã�ʧ���������ǡ�')
    if data['missing_tasks']:paragraph('����δƥ�䵽����Ŀ��'+'��'.join(data['missing_tasks']))
    for r in data['runs']:
        paragraph(r['run_id']+' '+r['task_name'],'Heading1')
        paragraph('���״̬��'+r['status']+'��ִ��ʱ�䣺'+r['start']+' �� '+(r['end'] or 'δȡ��')+'��')
        paragraph('��ʱ '+fmt_duration(r['values']['seconds'],True)+'��LLM��ɵ��� '+text_metric('llm_calls',r['values']['llm_calls'])+'�Σ����ߵ��� '+text_metric('tool_calls',r['values']['tool_calls'])+'�Σ���ȷ���ߴ��� '+text_metric('tool_errors',r['values']['tool_errors'])+'�Ρ�')
        if r['conditions']:paragraph('ʵ�����󸽼�������'+r['conditions'])
        if r['segment_count']>1:
            paragraph('���ΰ���׷�Ӽ�������������ԭֵ�����ʱ '+fmt_duration(r['active_seconds'],True)+'���μ�ȴ� '+fmt_duration(r['gap_seconds'],True)+'��')
            table(['ִ�ж�','����','��ʱ','LLM��ɵ�����'],[[s['segment'],s['segment_kind'],fmt_duration(s['values']['seconds'],True),text_metric('llm_calls',s['values']['llm_calls'])] for s in r['segments']],[800,1900,3800,2500])
        for o in r.get('page_observations',[]):
            paragraph('ҳ��۲죺'+o.get('page_status','δ��¼')+'��ʵ�ʻظ���'+o.get('page_response','δ��¼')+'��֤����Դ��'+o['evidence'])
        for warning in dict.fromkeys(r['warnings']):paragraph('��¼���⣺'+warning)
        if r['errors']:
            table(['ʱ���빤��','��ȷ����ժҪ','���ü���־��λ'],[[short_time(e['time'])+'\n'+e['name'],error_summary(e),e['id']+'\n'+e['evidence']] for e in r['errors']],[1800,3600,3600])
        elif r['values']['tool_errors']==0:paragraph('��־�еĹ��ߴ���״̬���ڲ������Ǽ��˳���δ��¼��ȷ���ߴ���')
        else:paragraph('���ߴ�������ȱʧ�����м�¼��δ��������ִ������JSON��')
        if r['retries'] is None:paragraph('ģ��ʧ�����Դ���δȡ������֤�ݣ�����Ϊδ��¼��')
        elif r['retries']:
            table(['ʱ��','ģ�����Լ�¼','�¼���λ'],[[short_time(e['time']),e['message'],e['id']] for e in r['retries']],[2000,4200,2800])
        if analysis and r['run_id'] in analysis:
            for note in analysis[r['run_id']]:paragraph(note)
        if r['files']:
            for f in r['files']:paragraph(('�ļ����ڣ�' if f['exists'] else '�Ǽ�·�������ڣ�')+f['path'],'Small')
        else:paragraph('δ�ҵ��ɹ��������ߵǼ��Ҵ��̴��ڵ��ļ���')
        paragraph('��Դ '+r['source']+'\n�Ự '+r['session']+'\n�״��û���Ϣ '+r['user_message'],'Small')
    method_paragraphs=[paragraph('�ɼ������븴��','Heading1'),paragraph(data['meta']['method']),
        paragraph('���ߵ��ú��������ð���ʶ����������������ȷ״̬���ڲ���Ǽ��˳��롣Token��ȡ��ε����ֶβ��ۼӣ����������ʰ������ȡ�������ۼ����������㡣���������������Դ���������ָ���Ϊԭ���Ժ����Ρ�'),
        paragraph('������������ֻ����ȡһ�¡�����ժҪ '+data['meta']['snapshot_sha256'],'Small')]
    if data['excluded']:method_paragraphs.append(paragraph(f"����{len(data['excluded'])}��δ����Ŀ������ĻỰ����Ϣ��¼��ԭ�򱣴���ִ������JSON��"))
    for pp in method_paragraphs:child(pp.find('{'+W+'}pPr'),W,'keepNext')
    paragraph('��̨�Ự����ƥ����Ŀ����Ϣ���������ָ�ꡣ','Small')
    sec=child(body,W,'sectPr');child(sec,W,'pgSz',{'{'+W+'}w':'11906','{'+W+'}h':'16838'});child(sec,W,'pgMar',{'{'+W+'}top':'960','{'+W+'}bottom':'960','{'+W+'}left':'1080','{'+W+'}right':'1080','{'+W+'}header':'360','{'+W+'}footer':'360'})
    styles=el(W,'styles')
    for name,size,bold in [('Normal',21,False),('Title',40,True),('Heading1',28,True),('Small',18,False),('TableText',17,False),('TableHeader',17,True)]:
        s=child(styles,W,'style',{'{'+W+'}type':'paragraph','{'+W+'}styleId':name})
        if name=='Normal':s.set('{'+W+'}default','1')
        child(s,W,'name',{'{'+W+'}val':name});p=child(s,W,'pPr')
        child(p,W,'spacing',{'{'+W+'}before':'180' if name=='Heading1' else '0','{'+W+'}after':'130' if name in ('Normal','Small') else '140' if name=='Heading1' else '40','{'+W+'}line':'270','{'+W+'}lineRule':'auto'})
        if name in ('Title','Heading1'):child(p,W,'keepNext')
        r=child(s,W,'rPr');child(r,W,'rFonts',{'{'+W+'}ascii':'Microsoft YaHei','{'+W+'}hAnsi':'Microsoft YaHei','{'+W+'}eastAsia':'Microsoft YaHei'});child(r,W,'sz',{'{'+W+'}val':str(size)});child(r,W,'color',{'{'+W+'}val':'000000'})
        if bold:child(r,W,'b')
    content=el(CT,'Types')
    child(content,CT,'Default',{'Extension':'rels','ContentType':'application/vnd.openxmlformats-package.relationships+xml'})
    child(content,CT,'Default',{'Extension':'xml','ContentType':'application/xml'})
    for part,kind in [('document','document.main'),('styles','styles')]:child(content,CT,'Override',{'PartName':'/word/'+part+'.xml','ContentType':'application/vnd.openxmlformats-officedocument.wordprocessingml.'+kind+'+xml'})
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',xml(content));z.writestr('_rels/.rels',relationships([('rId1','officeDocument','word/document.xml')]))
        z.writestr('word/document.xml',xml(document));z.writestr('word/styles.xml',xml(styles));z.writestr('word/_rels/document.xml.rels',relationships([('rStyles','styles','styles.xml')]))


def export_reports(data,output,analysis=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    excel_report(data,output/(data['product']+'_����ָ��ͳ��.xlsx'))
    word_report(data,output/(data['product']+'_����������ɼ�����.docx'),analysis)


if __name__=='__main__':
    p=argparse.ArgumentParser(description='���Ѳɼ�JSON����ͬ�ھ�Excel��Word�������¶�ȡ��־')
    p.add_argument('data');p.add_argument('--output',required=True);p.add_argument('--analysis',help='��run_id��֯�Ĳ���۲�JSON��ÿ��Ϊ����־���ݵĶ�������')
    a=p.parse_args();export_reports(load(a.data),a.output,load(a.analysis) if a.analysis else None)
