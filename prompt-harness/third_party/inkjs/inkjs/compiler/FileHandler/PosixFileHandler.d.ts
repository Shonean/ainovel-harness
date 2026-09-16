import { IFileHandler } from "../IFileHandler";
export declare class PosixFileHandler implements IFileHandler {
    readonly rootPath: string;
    constructor(rootPath?: string);
    readonly ResolveInkFilename: (filename: string) => string;
    readonly LoadInkFileContents: (filename: string) => string;
}
