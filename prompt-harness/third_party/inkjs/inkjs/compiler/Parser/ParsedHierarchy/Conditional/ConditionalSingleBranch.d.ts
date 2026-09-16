import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Divert as RuntimeDivert } from "../../../../engine/Divert";
import { Expression } from "../Expression/Expression";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Story } from "../Story";
import { Weave } from "../Weave";
export declare class ConditionalSingleBranch extends ParsedObject {
    _contentContainer: RuntimeContainer | null;
    _conditionalDivert: RuntimeDivert | null;
    _ownExpression: Expression | null;
    _innerWeave: Weave | null;
    isTrueBranch: boolean;
    get ownExpression(): Expression | null;
    set ownExpression(value: Expression | null);
    matchingEquality: boolean;
    isElse: boolean;
    isInline: boolean;
    returnDivert: RuntimeDivert | null;
    constructor(content?: ParsedObject[] | null | undefined);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    readonly GenerateRuntimeForContent: () => RuntimeContainer;
    ResolveReferences(context: Story): void;
}
