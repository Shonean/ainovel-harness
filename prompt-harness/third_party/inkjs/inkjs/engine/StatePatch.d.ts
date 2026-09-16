import { InkObject } from "./Object";
import { Container } from "./Container";
export declare class StatePatch {
    get globals(): Map<string, InkObject>;
    get changedVariables(): Set<string>;
    get visitCounts(): Map<Container, number>;
    get turnIndices(): Map<Container, number>;
    constructor();
    constructor(toCopy: StatePatch | null);
    TryGetGlobal(name: string | null, /* out */ value: InkObject | null): {
        result: InkObject | undefined;
        exists: boolean;
    } | {
        result: InkObject | null;
        exists: boolean;
    };
    SetGlobal(name: string, value: InkObject): void;
    AddChangedVariable(name: string): Set<string>;
    TryGetVisitCount(container: Container, /* out */ count: number): {
        result: number | undefined;
        exists: boolean;
    };
    SetVisitCount(container: Container, count: number): void;
    SetTurnIndex(container: Container, index: number): void;
    TryGetTurnIndex(container: Container, /* out */ index: number): {
        result: number | undefined;
        exists: boolean;
    };
    private _globals;
    private _changedVariables;
    private _visitCounts;
    private _turnIndices;
}
