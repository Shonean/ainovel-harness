import { Container as RuntimeContainer } from "../../../engine/Container";
import { DebugMetadata } from "../../../engine/DebugMetadata";
import { FindQueryFunc } from "./FindQueryFunc";
import { InkObject as RuntimeObject } from "../../../engine/Object";
import { Path as RuntimePath } from "../../../engine/Path";
import { Story } from "./Story";
export declare abstract class ParsedObject {
    abstract readonly GenerateRuntimeObject: () => RuntimeObject | null;
    private _alreadyHadError;
    private _alreadyHadWarning;
    private _debugMetadata;
    private _runtimeObject;
    content: ParsedObject[];
    parent: ParsedObject | null;
    get debugMetadata(): DebugMetadata | null;
    set debugMetadata(value: DebugMetadata | null);
    get hasOwnDebugMetadata(): boolean;
    get typeName(): string;
    readonly GetType: () => string;
    get story(): Story;
    get runtimeObject(): RuntimeObject;
    set runtimeObject(value: RuntimeObject);
    get runtimePath(): RuntimePath;
    get containerForCounting(): RuntimeContainer | null;
    get ancestry(): ParsedObject[];
    readonly AddContent: <T extends ParsedObject, V extends T | T[]>(subContent: V) => V | undefined;
    readonly InsertContent: <T extends ParsedObject>(index: number, subContent: T) => T;
    readonly Find: <T extends ParsedObject>(type: (new (...arg: any[]) => T) | (Function & {
        prototype: T;
    })) => (queryFunc?: FindQueryFunc<T> | null) => T | null;
    readonly FindAll: <T extends ParsedObject>(type: (new (...arg: any[]) => T) | (Function & {
        prototype: T;
    })) => (queryFunc?: FindQueryFunc<T>, foundSoFar?: T[]) => T[];
    ResolveReferences(context: Story): void;
    Error(message: string, source?: ParsedObject | null, isWarning?: boolean): void;
    readonly Warning: (message: string, source?: ParsedObject | null) => void;
}
