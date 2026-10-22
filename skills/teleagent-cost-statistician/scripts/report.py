"""Portable XLSX/DOCX report export; standard OOXML, no Office or third-party packages."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as E
from zipfile import ZipFile, ZIP_DEFLATED
from common import METRICS, LABELS, UNITS, load, timezone, sum_known

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
    if seconds is None:return '未取得'
    import math
    n=math.floor(seconds*(1000 if milliseconds else 1)+0.5);ss,ms=divmod(n,1000) if milliseconds else (n,0)
    h,rem=divmod(ss,3600);m,s=divmod(rem,60)
    return f'{h}时{m:02d}分{s:02d}'+(f'.{ms:03d}' if milliseconds else '')+'秒'
def text_metric(key,v,mean=False):
    if v is None:return '未取得'
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
    fmts=['#,##0','#,##0.0000','#,##0.000000','0.00%','[h]"时"mm"分"ss.000"秒"','[h]"时"mm"分"ss"秒"','yyyy-mm-dd hh:mm:ss.000','0.00']
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
                child(c,S,'f',text=d['f']);child(c,S,'v',text=v if v is not None else '未取得')
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
    raw=[list(map(lambda s:value(s,1),['测试ID','题号','独立次数','结果状态','开始时间','结束时间',*headers,'缓存读取Token 万','活动耗时 时分秒','段间等待 时分秒','执行分段数','模型记录','附加条件','会话ID','用户消息ID','来源文件','工作目录','交付路径','页面状态','页面回复','页面依据']))]
    segments=[list(map(lambda s:value(s,1),['测试ID','题号','执行段','段落性质','开始时间','结束时间',*headers,'用户消息ID','说明']))]
    agents=[[value(s,1) for s in ['测试ID','题号','执行段','层级','执行会话','父执行标识','LLM完成调用数','输入Token 万','输出Token 万','缓存读取Token 万','工具调用','工具错误','搜索调用','结束记录完整','来源文件','工作目录']]]
    raw[0].extend(value(s,1) for s in ['子Agent会话数','子日志覆盖','覆盖问题'])
    for r in runs:
        observations=r.get('page_observations',[])
        raw.append([r['run_id'],r['task'],r['attempt'],r['status'],value(excel_timestamp(r['start_ms'],tz),9),value(excel_timestamp(r['end_ms'],tz),9),
                    *[value(shown(k,r['values'][k]),metric_style(k)) for k in METRICS],value(r['cache_tokens']/10000 if r['cache_tokens'] is not None else None,4),
                    value(shown('seconds',r['active_seconds']),7),value(shown('seconds',r['gap_seconds']),7),r['segment_count'],' / '.join(r['models']),r['conditions'],
                    r['session'],r['user_message'],r['source'],r['directory'],'\n'.join(f['path'] for f in r['files']),
                    '\n'.join(o.get('page_status','') for o in observations),'\n'.join(o.get('page_response','') for o in observations),'\n'.join(o['evidence'] for o in observations)])
        coverage=r.get('agent_coverage',{})
        raw[-1].extend([len(coverage.get('child_sessions',[])),('完整' if coverage['complete'] else '不完整') if 'complete' in coverage else '未记录',
                        '\n'.join(i['reason']+' '+str(i.get('node',i.get('parent',''))) for i in coverage.get('issues',[]))])
        for s in r['segments']:
            segments.append([r['run_id'],r['task'],s['segment'],s['segment_kind'],value(excel_timestamp(s['start_ms'],tz),9),value(excel_timestamp(s['end_ms'],tz),9),
                             *[value(shown(k,s['values'][k]),metric_style(k)) for k in METRICS],s['user_message'],'；'.join(s['warnings'])])
            for a in s.get('agent_executions',[]):
                v=a['values']
                agents.append([r['run_id'],r['task'],s['segment'],a['depth'],a['session'],a.get('parent'),
                    value(v['llm_calls'],3),value(shown('input_tokens',v['input_tokens']),4),value(shown('output_tokens',v['output_tokens']),4),
                    value(a['cache_tokens']/10000 if a['cache_tokens'] is not None else None,4),v['tool_calls'],v['tool_errors'],v['search_calls'],
                    '是' if a['complete'] else '否',a['source'],a.get('directory')])
    summary=[[value(data['product']+' 端侧日志统计',2)],[],['逐次与分段耗时保留毫秒，均值显示至秒；Token以万为单位。'],[],[],[value(s,1) for s in ['题号','题目','独立次数',*headers]]]
    limit=max(201,len(raw)+100); rawcols=[col(i) for i in range(7,16)]
    def rg(c):return f"'逐次记录'!${c}$2:${c}${limit}"
    for row,t in enumerate(tasks,7):
        cells=[t['id'],t['name'],value(sum(r['task']==t['id'] for r in runs),3,f'COUNTIF({rg("B")},A{row})')]
        for i,k in enumerate(METRICS):
            rr=rg(rawcols[i]);n=f'COUNTIFS({rg("B")},A{row},{rr},"<>")'
            formula=f'IF({n}=0,"未取得",SUMIFS({rr},{rg("B")},A{row})/{n})'
            cells.append(value(shown(k,data['summary'][k]['by_task'][t['id']]['mean']),metric_style(k,True),formula))
        summary.append(cells)
    summary.append([]);lasttask=6+len(tasks)
    for which,label in [('task_mean','对象均值 题目等权'),('run_mean','全部测试均值 按次'),('task_n','有效题目数'),('run_n','有效测试次数')]:
        cells=[label,None,len({r['task'] for r in runs}) if which=='task_mean' else len(runs) if which=='run_mean' else None]
        for i,k in enumerate(METRICS):
            dest=col(4+i);rr=rg(rawcols[i]);taskrange=f'{dest}7:{dest}{lasttask}'
            target=taskrange if which in ('task_mean','task_n') else rr
            formula=f'COUNT({target})' if which.endswith('_n') else f'IF(COUNT({target})=0,"未取得",AVERAGE({target}))'
            cells.append(value(data['summary'][k][which] if which.endswith('_n') else shown(k,data['summary'][k][which]),3 if which.endswith('_n') else metric_style(k,True),formula))
        summary.append(cells)
    summary.append([])
    paired_den=f'SUMIFS({rg("I")},{rg("P")},"<>")';paired_num=f'SUMIFS({rg("P")},{rg("I")},"<>")'
    summary.append(['整体缓存命中率 输入量加权',None,None,None,None,None,None,value(data['pooled_cache_rate'],6,f'IF({paired_den}=0,"未取得",{paired_num}/{paired_den})')])
    for note in ['主要对象均值先求同题各次均值，再按题目等权；按次均值另列。',
                 '缺失值不补零；有完整数值的失败执行参与统计。每项列出有效题目数和次数。',
                 '完整重新提交算新一次；继续指令为原测试后续段；内部模型重试不增加独立测试次数。',
                 'LLM完成调用数仅计已完成且有用量记录的调用；失败重试另记。',
                 f'公式预留逐次记录至第{limit}行；超过时重新运行采集并生成报告。']:
        summary.append([note])
    definitions=[['耗时','时分秒','用户提交至最后完成模型回复；完整测试含段间等待，分段表与活动耗时另列。'],
                 ['LLM完成调用数','次','已完成且有用量记录的LLM调用，按消息或请求标识去重；失败重试单列。'],
                 ['输入Token','万','按各次调用累加，含缓存读取及写入；同一上下文在多轮中再次输入仍会计量。'],
                 ['输出Token','万','读取输出字段后累加，1万等于10000 Token。'],
                 ['缓存命中率','%','缓存读取Token/累计输入Token；各题比例的算术均值为主，输入量加权值另列。'],
                 ['工具调用','次','按工具调用标识去重计数，包括失败调用。'],
                 ['工具错误','次','明确错误状态、内部错误标记或非零命令退出码，按调用标识去重。'],
                 ['搜索调用','次','仅统计本对象专用搜索工具调用，包括报错调用；网页抓取和命令间接联网单列观察。'],
                 ['交付文件数','个','成功交付工具登记且磁盘存在的唯一路径数；未登记文件不自动纳入。']]
    method=[[value('指标口径与来源',2)],[],[value(x,1) for x in ['指标','单位','口径']],*definitions,[],
            ['对象方法',None,data['meta']['method']],['数据来源',None,data['meta']['source']],['采集时间',None,data['meta']['collected_at']],
            ['时区',None,data['meta']['timezone']],['两次读取一致',None,str(data['meta']['read_twice_equal'])],
            ['数据快照摘要',None,data['meta']['snapshot_sha256']],['未匹配题目',None,'、'.join(data['missing_tasks']) or '无']]
    sheets=[('任务均值',summary,[33,32,12,23,22,21,21,18,18,18,18,18],6,None),
            ('逐次记录',raw,[25,11,12,42,29,29,25,22,20,20,18,18,18,18,18,22,25,25,14,25,45,45,45,65,65,65,22,45,45,18,18,65],1,1),
            ('执行分段',segments,[25,11,12,18,29,29,25,22,20,20,18,18,18,18,18,45,48],1,1),
            ('Agent明细',agents,[25,12,12,10,44,60,20,22,22,22,18,18,18,20,80,65],1,1),
            ('口径与来源',method,[27,14,115],0,None)]
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
            merges=([f'A{j}:L{j}' for j,row in enumerate(rows,1) if row and len(row)==1] if name=='任务均值' else ['A1:C1'] if name=='口径与来源' else [])
            z.writestr(f'xl/worksheets/sheet{i}.xml',worksheet(rows,widths,freeze,autofilter,merges))
        child(workbook,S,'calcPr',{'calcId':'191029','fullCalcOnLoad':'1'})
        rels.append(('rStyles','styles','styles.xml'))
        z.writestr('[Content_Types].xml',xml(content));z.writestr('_rels/.rels',relationships([('rId1','officeDocument','xl/workbook.xml')]))
        z.writestr('xl/workbook.xml',xml(workbook));z.writestr('xl/_rels/workbook.xml.rels',relationships(rels));z.writestr('xl/styles.xml',excel_styles())


def clean(text):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(text)).replace('“','').replace('”','').replace('"','').replace('—','，')


def error_summary(error):
    text=error['message'];code=error.get('exit_code')
    patterns=[('SEARCH_TIME_RANGE_TOO_LONG','搜索时间范围超过七天限制'),
              ('SEARCH_TIME_RANGE_UNSUPPORTED','搜索工具不支持所请求的历史时间区间'),
              ('Unsupported browser URL protocol','浏览器不支持请求中的URL协议'),
              ('A positional parameter cannot be found','命令参数位置错误'),
              ('file: command not found','file命令不存在'),
              ('SyntaxError','脚本语法错误'),
              ('Cannot display content of binary file','读取工具不支持直接显示二进制文件'),
              ('File does not exist','目标文件不存在'),
              ('AssertionError','检查脚本断言失败'),
              ('failed to read file','图像工具读取文件失败'),
              ('cannot find the path','请求的文件路径不存在')]
    http=re.search(r'status code:\s*(\d+)',text)
    if http:return '网页抓取返回HTTP '+http.group(1)
    if 'punycode' in text and code not in (None,0):return f'命令退出码{code}；输出含Node弃用提示，根因需进一步核查'
    for pattern,summary in patterns:
        if pattern in text:return summary+(f'；退出码{code}' if code not in (None,0) else '')
    if 'timed out' in text or 'deadline exceeded' in text:return '网页抓取超时'
    return text[:160]+('…' if len(text)>160 else '')


def short_time(text):
    return text[:10]+'\n'+text[11:23] if text else '时间未记录'


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
    paragraph(data['product']+' 端侧日志观察与采集方法','Title')
    paragraph('采集时间 '+data['meta']['collected_at']+'    时区 '+data['meta']['timezone'])
    paragraph(f"本批找到{len(data['runs'])}次独立测试，{sum(r['segment_count'] for r in data['runs'])}个执行段。每次执行的九项指标、题目均值和对象均值保存在同名Excel。")
    paragraph('测试条件与对象均值','Heading1')
    models=sorted({m for r in data['runs'] for m in r['models']})
    paragraph('模型：'+('、'.join(models) or '未记录'))
    scope='按执行标识去重；耗时取整体起止时间，交付文件需主任务登记且存在。完整重提为新测试，继续并入原测试。'
    if any(r.get('agent_coverage',{}).get('child_sessions') for r in data['runs']):scope='计入实际委派的各层子Agent。'+scope
    paragraph(scope,'Small')
    paragraph('来源 '+data['meta']['source'],'Small')
    if data['excluded']:paragraph(f"{len(data['excluded'])}条未关联记录未计入，详情见JSON。",'Small')
    table(['指标','对象均值','有效题目数','有效次数'],[[a+' '+u,text_metric(k,data['summary'][k]['task_mean'],True),data['summary'][k]['task_n'],data['summary'][k]['run_n']] for k,a,u in zip(METRICS,LABELS,UNITS)],[2400,3000,1800,1800])
    paragraph('对象均值按题目等权计算；缺失值不计入均值，已取得完整数值的失败执行参与统计。')
    if data['missing_tasks']:paragraph('本批未匹配到的题目：'+'、'.join(data['missing_tasks']))
    for r in data['runs']:
        paragraph(r['run_id']+' '+r['task_name'],'Heading1')
        paragraph('结果状态：'+r['status']+('。执行时间：' if r.get('agent_coverage',{}).get('complete',True) else '。已知记录时间：')+r['start']+' 至 '+(r['end'] or '未取得')+'。')
        coverage=r.get('agent_coverage',{})
        if coverage:
            if not coverage.get('complete'):paragraph('子Agent日志不完整，相关指标未取得；已记录的调用明细保留在Excel和JSON。')
            for issue in coverage.get('issues',[]):paragraph('覆盖问题：'+issue['reason']+'；来源 '+str(issue.get('node',issue.get('parent',''))),'Small')
            contributions=r.get('agent_executions',[])
            rows=[]
            for is_child,label in [(False,'主Agent'),(True,'子Agent合计')]:
                selected=[a['values'] for a in contributions if bool(a['depth'])==is_child]
                if selected:rows.append([label,*[text_metric(k,sum_known(a[k] for a in selected)) for k in ('llm_calls','tool_calls','tool_errors','search_calls')]])
            if len(rows)>1:table(['已读取范围','LLM调用','工具调用','工具错误','搜索调用'],rows,[2200,1700,1700,1700,1700])
        paragraph('耗时 '+fmt_duration(r['values']['seconds'],True)+'；LLM完成调用 '+text_metric('llm_calls',r['values']['llm_calls'])+'次；工具调用 '+text_metric('tool_calls',r['values']['tool_calls'])+'次；明确工具错误 '+text_metric('tool_errors',r['values']['tool_errors'])+'次。')
        if r['conditions']:paragraph('实际请求附加条件：'+r['conditions'])
        if r['segment_count']>1:
            paragraph('本次包含追加继续，保留各段原值。活动耗时 '+fmt_duration(r['active_seconds'],True)+'，段间等待 '+fmt_duration(r['gap_seconds'],True)+'。')
            table(['执行段','性质','耗时','LLM完成调用数'],[[s['segment'],s['segment_kind'],fmt_duration(s['values']['seconds'],True),text_metric('llm_calls',s['values']['llm_calls'])] for s in r['segments']],[800,1900,3800,2500])
        for o in r.get('page_observations',[]):
            paragraph('页面观察：'+o.get('page_status','未记录')+'；实际回复：'+o.get('page_response','未记录')+'。证据来源：'+o['evidence'])
        for warning in dict.fromkeys(r['warnings']):
            if not warning.startswith('子Agent执行记录不完整，'):paragraph('记录问题：'+warning)
        if r['errors']:
            table(['时间与工具','明确错误摘要','执行会话、调用及日志定位'],[[short_time(e['time'])+'\n'+e['name'],error_summary(e),(e.get('agent_session','')+'\n' if e.get('agent_session') else '')+e['id']+'\n'+e['evidence'].split('#')[-1]] for e in r['errors']],[1800,3600,3600])
        if r['retries']:
            table(['时间','模型重试记录','事件定位'],[[short_time(e['time']),e['message'],e['id']] for e in r['retries']],[2000,4200,2800])
        if analysis and r['run_id'] in analysis:
            for note in analysis[r['run_id']]:paragraph(note)
        if r['files']:
            for f in r['files']:paragraph(('文件存在：' if f['exists'] else '登记路径不存在：')+f['path'],'Small')
        paragraph('会话 '+r['session']+'\n首次用户消息 '+r['user_message'],'Small')
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
    excel_report(data,output/(data['product']+'_过程指标统计.xlsx'))
    word_report(data,output/(data['product']+'_过程问题与采集方法.docx'),analysis)


if __name__=='__main__':
    p=argparse.ArgumentParser(description='从已采集JSON生成同口径Excel和Word，不重新读取日志')
    p.add_argument('data');p.add_argument('--output',required=True);p.add_argument('--analysis',help='按run_id组织的补充观察JSON，每项为有日志依据的段落数组')
    a=p.parse_args();export_reports(load(a.data),a.output,load(a.analysis) if a.analysis else None)
