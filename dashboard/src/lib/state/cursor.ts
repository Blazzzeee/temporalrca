import { writable } from 'svelte/store';
export const correlationCursor = writable<number | null>(null);
export function closestCursorIndex(values:number[],target:number){let best=0;for(let i=1;i<values.length;i++)if(Math.abs(values[i]-target)<Math.abs(values[best]-target))best=i;return best;}
export function moveCursorIndex(current:number,key:string,length:number){if(key==='ArrowLeft')return Math.max(0,current-1);if(key==='ArrowRight')return Math.min(length-1,current+1);return current;}
