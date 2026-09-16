import { ErrorHandler } from "../engine/Error";
import { IFileHandler } from "./IFileHandler";
export declare class CompilerOptions {
    readonly sourceFilename: string | null;
    readonly pluginNames: string[];
    readonly countAllVisits: boolean;
    readonly errorHandler: ErrorHandler | null;
    readonly fileHandler: IFileHandler | null;
    constructor(sourceFilename?: string | null, pluginNames?: string[], countAllVisits?: boolean, errorHandler?: ErrorHandler | null, fileHandler?: IFileHandler | null);
}
