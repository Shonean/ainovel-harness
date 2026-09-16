import { VariablePointerValue } from "./Value";
import { VariableAssignment } from "./VariableAssignment";
import { InkObject } from "./Object";
import { ListDefinitionsOrigin } from "./ListDefinitionsOrigin";
import { CallStack } from "./CallStack";
import { StatePatch } from "./StatePatch";
import { SimpleJson } from "./SimpleJson";
import { InkList } from "./Story";
import { Path } from "./Path";
type VariableStateValue = boolean | string | number | InkList | Path | null;
declare const VariablesState_base: new () => Pick<Record<string, any>, string>;
export declare class VariablesState extends VariablesState_base {
    variableChangedEventCallbacks: Array<(variableName: string, newValue: InkObject) => void>;
    variableChangedEvent(variableName: string, newValue: InkObject): void;
    patch: StatePatch | null;
    StartVariableObservation(): void;
    CompleteVariableObservation(): Map<string, any>;
    NotifyObservers(changedVars: Map<string, any>): void;
    get callStack(): CallStack;
    set callStack(callStack: CallStack);
    $(variableName: string): VariableStateValue;
    $(variableName: string, value: VariableStateValue): void;
    constructor(callStack: CallStack, listDefsOrigin: ListDefinitionsOrigin | null);
    ApplyPatch(): undefined;
    SetJsonToken(jToken: Record<string, any>): undefined;
    static dontSaveDefaultValues: boolean;
    WriteJson(writer: SimpleJson.Writer): void;
    RuntimeObjectsEqual(obj1: InkObject | null, obj2: InkObject | null): boolean;
    GetVariableWithName(name: string | null, contextIndex?: number): InkObject | null;
    TryGetDefaultVariableValue(name: string | null): InkObject | null;
    GlobalVariableExistsWithName(name: string): boolean;
    GetRawVariableWithName(name: string | null, contextIndex: number): InkObject | null;
    ValueAtVariablePointer(pointer: VariablePointerValue): InkObject | null;
    Assign(varAss: VariableAssignment, value: InkObject): undefined;
    SnapshotDefaultGlobals(): void;
    RetainListOriginsForAssignment(oldValue: InkObject, newValue: InkObject): void;
    SetGlobal(variableName: string | null, value: InkObject): undefined;
    ResolveVariablePointer(varPointer: VariablePointerValue): VariablePointerValue;
    GetContextIndexOfVariableNamed(varName: string): number;
    /**
     * This function is specific to the js version of ink. It allows to register a
     * callback that will be called when a variable changes. The original code uses
     * `state.variableChangedEvent += callback` instead.
     *
     * @param {function} callback
     */
    ObserveVariableChange(callback: (variableName: string, newValue: InkObject) => void): void;
    private _globalVariables;
    private _defaultGlobalVariables;
    private _callStack;
    private _changedVariablesForBatchObs;
    private _listDefsOrigin;
    private _batchObservingVariableChanges;
}
export {};
