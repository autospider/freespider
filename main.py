# -*- coding: utf-8 -*-
import sys
import asyncio
import aiohttp
from copy import deepcopy
from datetime import datetime
from aiohttp.client_exceptions import ClientProxyConnectionError
from extract import getTaskId, transform, doParse, loads, jsonpath
from proxy import getProxy
from store import Store


async def aRequest(pTask):
    iTask = deepcopy(pTask)
    iResult = {
        'body': b'',
        'status': 200,
        'reqDate': datetime.now(),
        'resDate': datetime.now(),
        'msg': 'success',
        'url': iTask.get('req', {}).get('url')
    }
    if iTask.get('skip', False):
        iTask['res'] = iResult
        return iTask

    iReqTask = deepcopy(iTask.get('req', {}))

    iHeaders = {
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        'Accept-Encoding': 'gzip, deflate',
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/90.0.4430.212 Safari/537.36 "
    }
    iHeaders.update(iReqTask.get('headers', {}))
    iReqTask['headers'] = iHeaders
    iReqTask['timeout'] = aiohttp.ClientTimeout(total=int(iReqTask.get('timeout', 60)))
    iReqTask.setdefault('method', 'GET')
    iReqTask.setdefault('proxy', await getProxy(iTask))
    iResult['reqDate'] = datetime.now()
    async with aiohttp.ClientSession() as iSession:
        try:
            async with iSession.request(**iReqTask) as iRes:
                iResult['headers'] = dict(iRes.headers)
                iResult['body'] = await iRes.read()
                iResult['cookies'] = {k: dict(iRes.cookies[k]) for k in iRes.cookies}
                iResult['status'] = iRes.status
                iResult['url'] = str(iRes.real_url)
        except ClientProxyConnectionError:
            iResult['status'] = -3
            iResult['msg'] = 'ClientProxyConnectionError'
        except asyncio.TimeoutError:
            iResult['status'] = -2
            iResult['msg'] = 'TimeoutError'
        except Exception as e:
            iResult['status'] = -1
            iResult['msg'] = str(e)

    iResult['resDate'] = datetime.now()
    iTask['res'] = iResult
    return iTask


class DownloadCache(object):
    def __init__(self):
        self._history_lock = asyncio.Lock()
        self._history = set()

        self._queue_lock = asyncio.Lock()
        self._queue = list()
        self._counter = 0

    async def push(self, pTask):
        iList = []
        async with self._history_lock:
            for iTask in pTask['task']:
                iTaskId = getTaskId(iTask)
                if iTaskId not in self._history:
                    self._history.add(iTaskId)
                    iList.append(iTask)
        if iList:
            pTask['task'] = iList
            async with self._queue_lock:
                self._counter += 1
                self._queue.append(pTask)

    async def pop(self):
        async with self._queue_lock:
            iResult = self._queue.pop(0) if self._queue else None
        return iResult

    async def finish(self):
        async with self._queue_lock:
            self._counter -= 1

    def empty(self):
        return self._counter <= 0


class Spider(object):
    def __init__(self, pProject):
        self.project = pProject
        self.dlCache = DownloadCache()
        self.concurrency = self.project.get('concurrency', 5)
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.retryCode = self.project.get('retryCode', [])
        if isinstance(self.retryCode, list):
            self.retryCode = []
        self.retryCode += [-2, -3]
        self.retryCode = set(self.retryCode)
        self.store = Store()

    def log(self, *args):
        print(datetime.now(), self.project['taskKey'], *args)

    def makeReq(self, pTask):
        iTask = deepcopy(pTask)
        iTask.setdefault('taskKey', self.project['taskKey'])
        if 'req' not in iTask:
            iTask['skip'] = True
            iTask['local'] = True
            iTask['req'] = {"url": f"virtualReq|{self.project['taskKey']}|{iTask['actKey']}"}
        iTask['req'].setdefault('timeout', self.project.get('timeout', 60))

        if 'resource' in self.project:
            iTask.setdefault('resource', self.project['resource'])
            iTask.setdefault('resourceKey', self.project['resourceKey'])
            iTask.setdefault('interval', self.project.get('interval', 3))

        if 'fixProxy' in self.project:
            iTask.setdefault('fixProxy', self.project.get('fixProxy'))
        iTask['created'] = datetime.now()

        return iTask

    def makeStore(self, pTask):
        iTask = deepcopy(pTask)
        iTask['taskKey'] = self.project['taskKey']
        iTask['created'] = datetime.now()
        iTask['flag'] = 0
        return iTask

    async def download(self, pIndex, pTask, pCount=None):
        async with self.semaphore:
            iResult = await aRequest(pTask)
        if iResult['res']['status'] in self.retryCode:
            iCount = self.project.get('retryTimes', 3) if pCount is None else pCount - 1
            if iCount > 0:
                self.log(pIndex, 'retry', pTask.get('req', {}).get('url', 'virtualRequest'), iCount)
                iResult = await self.download(pIndex, pTask, iCount)
            else:
                self.log(pIndex, 'ignore', pTask.get('req', {}).get('url', 'virtualRequest'))
        else:
            self.log(pIndex, 'res', pTask.get('req', {}).get('url', 'virtualRequest'), iResult['res']['status'])
        return iResult

    async def pushTask(self, pTask):
        if pTask.get('isBatch', False):
            await self.dlCache.push(pTask)
        else:
            iList = pTask.pop('task')
            for iItem in iList:
                iTask = deepcopy(pTask)
                iTask['task'] = [iItem]
                await self.dlCache.push(iTask)

    def doParse(self, pTask):
        iActKey = pTask['actKey']
        iStep = self.project.get('parser', {}).get(iActKey, [])
        iList = transform(pTask)
        iResult = []
        for iItem in iList:
            iItem = doParse(iStep, iItem)
            iResult.extend(iItem) if isinstance(iItem, list) else iResult.append(iItem)
        return iResult

    def initSend(self, pData, pActKey):
        iResult = {}
        if not pData:
            return iResult

        for iSender in self.project.get('sender', {}).get(pActKey, []):
            iSender = deepcopy(iSender)
            iSource = iSender.pop('source', '*' if isinstance(pData, list) else '.')
            iSenderType = iSender.pop('type', 'any')
            iSenderType.setdefault('encoding', self.project.get('encoding', 'utf8'))
            iList = jsonpath(pData, iSource)
            if isinstance(iList, bool):
                continue
            iResult.setdefault(iSenderType, [])
            if iSenderType == 'req':
                iList = [self.makeReq(_) for _ in iList]
            elif iSenderType == 'store':
                iList = [self.makeStore(_) for _ in iList]

            iSender['task'] = iList
            iResult[iSenderType].append(iSender)

        return iResult

    async def doSend(self, pData, pActKey):
        iData = self.initSend(pData, pActKey)
        for iSenderType, iList in iData.items():
            if iSenderType == 'req':
                for iItem in iList:
                    iItem['actKey'] = iItem.pop('target')
                    iItem['dataType'] = iItem.pop('targetType', 'dom')
                    await self.pushTask(iItem)
            elif iSenderType == 'store':
                for iItem in iList:
                    await self.store.doStore(iItem)

    async def doTask(self, pIndex):
        while not self.dlCache.empty():
            iTask = await self.dlCache.pop()
            if iTask is None:
                await asyncio.sleep(0.1)
                continue
            iList = [self.download(pIndex, _) for _ in iTask['task']]
            iTask['task'] = await asyncio.gather(*iList)
            iResult = self.doParse(iTask)
            await self.doSend(iResult, iTask['actKey'])
            await self.dlCache.finish()

    async def start(self):
        iActKey = 'entrance'
        iEntrance = self.project.get(iActKey, {})
        iInherit = iEntrance.pop('inherit', {})
        iEntrance.setdefault('actKey', iActKey)
        iEntrance.setdefault('encoding', self.project.get('encoding', 'utf8'))
        iList = []
        for iItem in iEntrance.pop('task'):
            iItem = self.makeReq(iItem)
            iItem['inherit'] = deepcopy(iInherit)
            iList.append(iItem)
        iEntrance['task'] = iList
        await self.pushTask(iEntrance)
        await asyncio.wait([self.doTask(i) for i in range(self.concurrency)])


def main():
    iFileName = 'demo.json' if len(sys.argv) < 2 else sys.argv[1]
    with open(iFileName, 'rb') as f:
        iProject = loads(f.read())
        print(datetime.now(), 'start')
        iSpider = Spider(iProject)
        asyncio.run(iSpider.start())
        print(datetime.now(), 'over')


if __name__ == '__main__':
    main()
