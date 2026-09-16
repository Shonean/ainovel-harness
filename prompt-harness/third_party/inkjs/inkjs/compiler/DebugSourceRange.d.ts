import { DebugMetadata } from "../engine/DebugMetadata";
export declare class DebugSourceRange {
    readonly length: number;
    readonly debugMetadata: DebugMetadata | null;
    text: string;
    constructor(length: number, debugMetadata: DebugMetadata | null, text: string);
}
