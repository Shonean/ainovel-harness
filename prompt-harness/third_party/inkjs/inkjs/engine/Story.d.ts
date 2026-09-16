import { Container } from "./Container";
import { InkObject } from "./Object";
import { StoryState } from "./StoryState";
import { ChoicePoint } from "./ChoicePoint";
import { Choice } from "./Choice";
import { Path } from "./Path";
import { ListDefinitionsOrigin } from "./ListDefinitionsOrigin";
import { ListDefinition } from "./ListDefinition";
import { Pointer } from "./Pointer";
import { DebugMetadata } from "./DebugMetadata";
import { SimpleJson } from "./SimpleJson";
import { ErrorHandler } from "./Error";
export { InkList } from "./InkList";
export declare class Story extends InkObject {
    static inkVersionCurrent: number;
    inkVersionMinimumCompatible: number;
    get currentChoices(): Choice[];
    get currentText(): string | null;
    get currentTags(): string[] | null;
    get currentErrors(): string[] | null;
    get currentWarnings(): string[] | null;
    get currentFlowName(): string;
    get currentFlowIsDefaultFlow(): boolean;
    get aliveFlowNames(): string[] | null;
    get hasError(): boolean;
    get hasWarning(): boolean;
    get variablesState(): import("./VariablesState").VariablesState;
    get listDefinitions(): ListDefinitionsOrigin | null;
    get state(): StoryState;
    onError: ErrorHandler | null;
    onDidContinue: (() => void) | null;
    onMakeChoice: ((arg1: Choice) => void) | null;
    onEvaluateFunction: ((arg1: string, arg2: any[]) => void) | null;
    onCompleteEvaluateFunction: ((arg1: string, arg2: any[], arg3: string, arg4: any) => void) | null;
    onChoosePathString: ((arg1: string, arg2: any[]) => void) | null;
    StartProfiling(): void;
    EndProfiling(): void;
    constructor(contentContainer: Container, lists: ListDefinition[] | null);
    constructor(jsonString: string);
    constructor(json: Record<string, any>);
    ToJson(writer?: SimpleJson.Writer): string | void;
    ResetState(): void;
    ResetErrors(): undefined;
    ResetCallstack(): undefined;
    ResetGlobals(): void;
    SwitchFlow(flowName: string): void;
    RemoveFlow(flowName: string): void;
    SwitchToDefaultFlow(): void;
    Continue(): string | null;
    get canContinue(): boolean;
    get asyncContinueComplete(): boolean;
    ContinueAsync(millisecsLimitAsync: number): void;
    ContinueInternal(millisecsLimitAsync?: number): void;
    ContinueSingleStep(): boolean;
    CalculateNewlineOutputStateChange(prevText: string | null, currText: string | null, prevTagCount: number, currTagCount: number): Story.OutputStateChange;
    ContinueMaximally(): string;
    ContentAtPath(path: Path): import("./SearchResult").SearchResult;
    KnotContainerWithName(name: string): Container | null;
    PointerAtPath(path: Path): Pointer;
    StateSnapshot(): void;
    RestoreStateSnapshot(): void;
    DiscardSnapshot(): void;
    CopyStateForBackgroundThreadSave(): StoryState;
    BackgroundSaveComplete(): void;
    Step(): void;
    VisitContainer(container: Container, atStart: boolean): void;
    private _prevContainers;
    VisitChangedContainersDueToDivert(): void;
    PopChoiceStringAndTags(tags: string[]): string | null;
    ProcessChoice(choicePoint: ChoicePoint): Choice | null;
    IsTruthy(obj: InkObject): boolean;
    PerformLogicAndFlowControl(contentObj: InkObject | null): boolean;
    ChoosePathString(path: string, resetCallstack?: boolean, args?: any[]): void;
    IfAsyncWeCant(activityStr: string): void;
    ChoosePath(p: Path, incrementingTurnIndex?: boolean): void;
    ChooseChoiceIndex(choiceIdx: number): undefined;
    HasFunction(functionName: string): boolean;
    EvaluateFunction(functionName: string, args?: any[], returnTextOutput?: boolean): Story.EvaluateFunctionTextOutput | any;
    EvaluateExpression(exprContainer: Container): InkObject | null;
    allowExternalFunctionFallbacks: boolean;
    CallExternalFunction(funcName: string | null, numberOfArguments: number): undefined;
    BindExternalFunctionGeneral(funcName: string, func: Story.ExternalFunction, lookaheadSafe?: boolean): void;
    TryCoerce(value: any): any;
    BindExternalFunction(funcName: string, func: Story.ExternalFunction, lookaheadSafe?: boolean): void;
    UnbindExternalFunction(funcName: string): void;
    ValidateExternalBindings(): void;
    ValidateExternalBindings(c: Container | null, missingExternals: Set<string>): void;
    ValidateExternalBindings(o: InkObject | null, missingExternals: Set<string>): void;
    ObserveVariable(variableName: string, observer: Story.VariableObserver): void;
    ObserveVariables(variableNames: string[], observers: Story.VariableObserver[]): void;
    RemoveVariableObserver(observer?: Story.VariableObserver, specificVariableName?: string): void;
    VariableStateDidChangeEvent(variableName: string, newValueObj: InkObject): void;
    get globalTags(): string[] | null;
    TagsForContentAtPath(path: string): string[] | null;
    TagsAtStartOfFlowContainerWithPathString(pathString: string): string[] | null;
    BuildStringOfHierarchy(): string;
    BuildStringOfContainer(container: Container): string;
    NextContent(): void;
    IncrementContentPointer(): boolean;
    TryFollowDefaultInvisibleChoice(): boolean;
    NextSequenceShuffleIndex(): number;
    Error(message: string, useEndLineNumber?: boolean): never;
    Warning(message: string): void;
    AddError(message: string, isWarning?: boolean, useEndLineNumber?: boolean): void;
    Assert(condition: boolean, message?: string | null): void;
    get currentDebugMetadata(): DebugMetadata | null;
    get mainContentContainer(): Container;
    /**
     * `_mainContentContainer` is almost guaranteed to be set in the
     * constructor, unless the json is malformed.
     */
    private _mainContentContainer;
    private _listDefinitions;
    private _externals;
    private _variableObservers;
    private _hasValidatedExternals;
    private _temporaryEvaluationContainer;
    /**
     * `state` is almost guaranteed to be set in the constructor, unless
     * using the compiler-specific constructor which will likely not be used in
     * the real world.
     */
    private _state;
    private _asyncContinueActive;
    private _stateSnapshotAtLastNewline;
    private _sawLookaheadUnsafeFunctionAfterNewline;
    private _recursiveContinueCount;
    private _asyncSaving;
    private _profiler;
}
export declare namespace Story {
    enum OutputStateChange {
        NoChange = 0,
        ExtendedBeyondNewline = 1,
        NewlineRemoved = 2
    }
    interface EvaluateFunctionTextOutput {
        returned: any;
        output: string;
    }
    interface ExternalFunctionDef {
        function: ExternalFunction;
        lookAheadSafe: boolean;
    }
    type VariableObserver = (variableName: string, newValue: any) => void;
    type ExternalFunction = (...args: any) => any;
}
