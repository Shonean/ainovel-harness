import { PushPopType } from "./PushPop";
import { Story } from "./Story";
import { Pointer } from "./Pointer";
import { InkObject } from "./Object";
import { SimpleJson } from "./SimpleJson";
export declare class CallStack {
    get elements(): CallStack.Element[];
    get depth(): number;
    get currentElement(): CallStack.Element;
    get currentElementIndex(): number;
    get currentThread(): CallStack.Thread;
    set currentThread(value: CallStack.Thread);
    get canPop(): boolean;
    constructor(storyContext: Story);
    constructor(toCopy: CallStack);
    Reset(): void;
    SetJsonToken(jObject: Record<string, any>, storyContext: Story): void;
    WriteJson(w: SimpleJson.Writer): void;
    PushThread(): void;
    ForkThread(): CallStack.Thread;
    PopThread(): void;
    get canPopThread(): boolean;
    get elementIsEvaluateFromGame(): boolean;
    Push(type: PushPopType, externalEvaluationStackHeight?: number, outputStreamLengthWithPushed?: number): void;
    CanPop(type?: PushPopType | null): boolean;
    Pop(type?: PushPopType | null): void;
    GetTemporaryVariableWithName(name: string | null, contextIndex?: number): InkObject | null;
    SetTemporaryVariable(name: string, value: any, declareNew: boolean, contextIndex?: number): void;
    ContextForVariableNamed(name: string): number;
    ThreadWithIndex(index: number): CallStack.Thread | null;
    get callStack(): CallStack.Element[];
    get callStackTrace(): string;
    _threads: CallStack.Thread[];
    _threadCounter: number;
    _startOfRoot: Pointer;
}
export declare namespace CallStack {
    class Element {
        currentPointer: Pointer;
        inExpressionEvaluation: boolean;
        temporaryVariables: Map<string, InkObject>;
        type: PushPopType;
        evaluationStackHeightWhenPushed: number;
        functionStartInOutputStream: number;
        constructor(type: PushPopType, pointer: Pointer, inExpressionEvaluation?: boolean);
        Copy(): Element;
    }
    class Thread {
        callstack: Element[];
        threadIndex: number;
        previousPointer: Pointer;
        constructor();
        constructor(jThreadObj: any, storyContext: Story);
        Copy(): Thread;
        WriteJson(writer: SimpleJson.Writer): undefined;
    }
}
