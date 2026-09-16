import { Container as RuntimeContainer } from "../../../../engine/Container";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
export declare abstract class Expression extends ParsedObject {
    abstract GenerateIntoContainer: (container: RuntimeContainer) => void;
    private _prototypeRuntimeConstantExpression;
    outputWhenComplete: boolean;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    readonly GenerateConstantIntoContainer: (container: RuntimeContainer) => void;
    get typeName(): string;
    Equals(obj: ParsedObject): boolean;
    readonly toString: () => string;
}
