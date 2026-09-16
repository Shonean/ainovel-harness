import { ConditionalSingleBranch } from "./ConditionalSingleBranch";
import { Expression } from "../Expression/Expression";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Story } from "../Story";
export declare class Conditional extends ParsedObject {
    initialCondition: Expression;
    branches: ConditionalSingleBranch[];
    private _reJoinTarget;
    constructor(initialCondition: Expression, branches: ConditionalSingleBranch[]);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    ResolveReferences(context: Story): void;
}
