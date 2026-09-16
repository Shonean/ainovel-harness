export declare class DebugMetadata {
    startLineNumber: number;
    endLineNumber: number;
    startCharacterNumber: number;
    endCharacterNumber: number;
    fileName: string | null;
    sourceName: string | null;
    Merge(dm: DebugMetadata): DebugMetadata;
    toString(): string;
}
