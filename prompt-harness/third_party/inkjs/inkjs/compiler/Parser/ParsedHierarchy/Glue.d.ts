import { Glue as RuntimeGlue } from "../../../engine/Glue";
import { Wrap } from "./Wrap";
export declare class Glue extends Wrap<RuntimeGlue> {
    constructor(glue: RuntimeGlue);
    get typeName(): string;
}
