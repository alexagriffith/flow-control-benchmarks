import assert from 'node:assert/strict';
import {build,validate,compare} from './model.mjs';
const expected={
 'rr/fcfs':[1,6,9,2,7,10,3,8,4,5],
 'rr/edf':[5,6,9,4,7,10,3,8,2,1],
 'rr/slo':[5,8,9,4,7,10,3,6,2,1],
 'strict/fcfs':[1,2,3,4,5,6,7,8,9,10],
 'strict/edf':[6,5,9,7,4,10,8,3,2,1],
 'strict/slo':[9,5,8,7,10,6,4,3,2,1],
 'las/fcfs':[6,7,8,9,10,1,2,3,4,5],
 'las/edf':[6,7,8,9,10,5,4,3,2,1],
 'las/slo':[8,7,6,9,10,5,4,3,2,1],
};
for(const [key,order] of Object.entries(expected))assert.deepEqual(build(...key.split('/')).turns.map(q=>q.id),order,key);
assert(compare({enqueue:1,ttl:9},{enqueue:2,ttl:8},'edf')<0,'EDF tie uses enqueue');
assert(compare({received:1,ttft:9,enqueue:5},{received:2,ttft:8,enqueue:4},'slo')<0,'SLO tie uses received, not enqueue');
console.log(validate());console.log('PASS: nine independently calculated dispatch orders and deadline tie-breaks');
