import { Path } from "./Path";
import { CallStack } from "./CallStack";
import { InkObject } from "./Object";
export declare class Choice extends InkObject {
    text: string;
    index: number;
    threadAtGeneration: CallStack.Thread | null;
    sourcePath: string;
    targetPath: Path | null;
    isInvisibleDefault: boolean;
    tags: string[] | null;
    originalThreadIndex: number;
    get pathStringOnChoice(): string;
    set pathStringOnChoice(value: string);
    Clone(): Choice;
}
