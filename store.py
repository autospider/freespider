# -*- coding: utf-8 -*-
import os
import csv
import asyncio
from bson.objectid import ObjectId
from bson.json_util import dumps


class Store(object):
    def __init__(self):
        self._lock = asyncio.Lock()

    async def storeCSV(self, pTask, pTarget):
        iTarget = pTarget
        if not iTarget.lower().endswith('.csv'):
            iTarget = iTarget + '.csv'
        iHeaders = tuple(pTask['task'][0].keys())
        if not os.path.exists(iTarget):
            async with self._lock:
                with open(iTarget, 'w', encoding='utf8') as iFile:
                    iWriter = csv.DictWriter(iFile, iHeaders)
                    iWriter.writeheader()
        elif not os.path.isfile(iTarget):
            print('error', f'{iTarget} is folder')
            return
        async with self._lock:
            with open(iTarget, 'a', encoding='utf8') as iFile:
                iWriter = csv.DictWriter(iFile, iHeaders)
                iWriter.writerows(pTask['task'])

    async def storeBSON(self, pTask, pTarget):
        iTarget = pTarget
        if not iTarget.lower().endswith('.bson'):
            iTarget = iTarget + '.bson'
        async with self._lock:
            with open(iTarget, 'a', encoding='utf8') as iFile:
                for iItem in pTask['task']:
                    iFile.write(dumps(iItem, ensure_ascii=False))
                    iFile.write('\n')

    async def doStore(self, pTask):
        if len(pTask['task']) == 0:
            return
        iTarget = pTask.get('target')
        iType = pTask.get('targetType')
        if iTarget is None or not isinstance(iTarget, str):
            iTarget = str(ObjectId())
        if iType == 'csv':
            await self.storeCSV(pTask, iTarget)
        elif iType == 'debug':
            pass
        else:
            await self.storeBSON(pTask, iTarget)

