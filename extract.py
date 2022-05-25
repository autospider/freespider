# -*- coding: utf-8 -*-
import re
import moment
import hashlib
import itertools
import lxml.html
from lxml import etree
from jsonpath import jsonpath
from urllib.parse import urljoin
from bson.json_util import loads
from datetime import datetime


class Element(etree.ElementBase):
    def __init__(self, pTag, pText=None, *args, **kwargs):
        super(Element, self).__init__(*args, **kwargs)
        self.tag = pTag
        if isinstance(pText, str):
            self.text = pText


def fnTokenize(_, pList, pVal):
    iResult = []
    for iItem in pList:
        iList = [i for i in re.findall(pVal, iItem) if i.strip()]
        if iList:
            iResult.extend(iList)
    return iResult


etree.FunctionNamespace('http://exslt.org/regular-expressions').prefix = 're'
ns = etree.FunctionNamespace(None)
ns['getValue'] = lambda context, pList, pVal: pList if pList else [pVal]
ns['string-join'] = lambda context, pList, pVal: pVal.join([re.sub(r'\s+', '', i) for i in pList])
ns['math-add'] = lambda context, pList, pVal: [Element(pTag="math", pText=str(int(i) + int(pVal))) for i in pList]
ns['cartesian'] = lambda context, pList, pStart, pEnd, pStep: [
    [str(i[0]), str(i[1])] for i in itertools.product(pList, range(int(pStart), int(pEnd), int(pStep)))]
ns['tokenize'] = fnTokenize


def node2str(pNode):
    return etree.tostring(pNode, method='c14n', pretty_print=True, strip_text=True, with_comments=False).decode()


def str2node(pHtml):
    iHtml = pHtml.replace('&nbsp;', ' ')
    iHtml = re.sub(r'\s+<', '<', re.sub(r'>\s+', '>', iHtml))
    iHtml = re.sub(r'<\?[^?]*\?>', '', re.sub('\r', '', iHtml))
    return etree.fromstring(iHtml, lxml.html.HTMLParser())


def obj2int(pObj):
    try:
        iResult = int(pObj)
    except:
        iResult = None
    return iResult


def getTaskId(pTask):
    iUrl = pTask.get('req', {}).get('url', '')
    iUrl = iUrl.split('://')[-1].split('#')[0]
    iBody = pTask['req'].get('body', '')
    iData = f"{iUrl}\x1f{iBody}"
    return hashlib.md5(iData.encode()).hexdigest()


def transform(pTask):
    iList, iCharSet = [], pTask.get('encoding', 'utf8')
    if pTask.get('dataType', 'dom') == 'dom':
        for iItem in pTask['task']:
            iBody = iItem['res'].pop('body', b'')
            iList.append({
                'parameter': str2node(iBody.decode(iCharSet, 'ignore')),
                'res': iItem['res'],
                'req': iItem['req'],
                'inherit': iItem.get('inherit', {})
            })
    elif pTask.get('dataType', 'dom') == 'json':
        for iItem in pTask['task']:
            iBody = iItem['res'].pop('body', b'')
            iList.append({
                'parameter': loads(re.sub('[\r\n]+', '', iBody.decode(iCharSet))),
                'res': iItem['res'],
                'req': iItem['req'],
                'inherit': iItem.get('inherit', {})
            })
    else:
        for iItem in pTask['task']:
            iBody = iItem['res'].pop('body', b'')
            iList.append({
                'parameter': iBody,
                'res': iItem['res'],
                'req': iItem['req'],
                'inherit': pTask.get('inherit', {})
            })
    return iList


def doParseStep(pStep, pData, pPrev):
    iParseData = pData.get(pStep.get('target', 'parameter'), pPrev)
    iType = pStep.get('parseType', 'any')
    iRule = pStep.get('rule')
    iOut = pStep.get('out', 'any')
    iNext = pStep.get('next', [])
    iFetchAll = pStep.get('fetchall', 0)

    iResult = iParseData
    if iParseData is not None:
        if iType == 'xpath':
            iResult = iParseData.xpath(iRule)
        elif iType == 'json':
            iResult = jsonpath(iParseData, iRule) or []
        elif iType == 'str':
            iResult = str.format_map(iRule, iParseData if isinstance(iParseData, dict) else {
                "parameter": iParseData})
        elif iType == 're':
            iResult = re.findall(iRule, iParseData)
        elif iType == 'value':
            iResult = iRule

    if not isinstance(iResult, list):
        iResult = [] if iResult is None else [iResult]

    if iOut == 'url':
        iResult = [urljoin(pData['res'].get('url', ''), i) for i in iResult]
    elif iOut == 'html':
        iResult = [node2str(i) for i in iResult]
    elif iOut == 'dom':
        iResult = [str2node(i) for i in iResult]
    elif iOut == 'range':
        iResult = list(range(*[int(i) for i in iResult])) if len(iResult) else []
    elif iOut == 'cartesianProduct':
        iResult = itertools.product(*iResult) if len(iResult) > 1 else []
    elif iOut == 'int':
        iResult = [obj2int(i) for i in iResult]
    elif iOut == 'datetime':
        iResult = [moment.date(str(i)).date if moment.date(str(i)).date else datetime.now() for i in iResult]

    if len(iResult):
        if iNext:
            return doParse(iNext, {
                'parameter': iResult[0], 'res': pData['res'], 'req': pData['req'], 'inherit': pData['inherit']
            }) if not iFetchAll else (doParse(iNext, {
                'parameter': iResult, 'res': pData['res'], 'req': pData['req'], 'inherit': pData['inherit']
            }) if iFetchAll > 0 else [doParse(iNext, {
                'parameter': list(i) if isinstance(i, tuple) else i,
                'res': pData['res'], 'req': pData['req'], 'inherit': pData['inherit']
            }) for i in iResult])
        else:
            return iResult if iFetchAll else iResult[0]
    else:
        return [] if iFetchAll else None


def doParse(pParseStep, pData):
    iResult = {}
    for iStep in pParseStep:
        iData = doParseStep(iStep, pData, iResult)
        if 'name' in iStep:
            if isinstance(iResult, dict):
                iResult[iStep['name']] = iData
            else:
                iResult = {iStep['name']: iData}
        else:
            if isinstance(iResult, list) and isinstance(iData, list):
                iResult.extend(iData)
            elif isinstance(iResult, dict) and isinstance(iData, dict):
                iResult.update(iData)
            else:
                iResult = iData
    return iResult


def main():
    # uvloop.install()
    # PServer(DownloadCache(), ParseCache(), ResultCache()).run()
    pass


if __name__ == '__main__':
    main()
