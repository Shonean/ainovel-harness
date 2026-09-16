import { Container as RuntimeContainer } from "../../../../engine/Container";
import { ParsedObject } from "../Object";
import { Divert } from "./Divert";
import { Divert as RuntimeDivert } from "../../../../engine/Divert";
import { DivertTargetValue } from "../../../../engine/Value";
import { Expression } from "../Expression/Expression";
import { Story } from "../Story";
export declare class DivertTarget extends Expression {
    private _runtimeDivert;
    get runtimeDivert(): RuntimeDivert;
    private _runtimeDivertTargetValue;
    get runtimeDivertTargetValue(): DivertTargetValue;
    divert: Divert;
    constructor(divert: Divert);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    ResolveReferences(context: Story): void;
    readonly Equals: (obj: ParsedObject) => boolean;
}
