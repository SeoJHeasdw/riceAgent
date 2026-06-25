export interface RuntimeStatus {
    running: boolean;
    port?: number;
    pid?: number;
}
/**
 * RuntimeManager handles the lifecycle of the RICE runtime process.
 * This is a stub implementation that will be expanded to manage the actual runtime.
 */
export declare class RuntimeManager {
    private process;
    private port;
    private isRunning;
    constructor();
    /**
     * Initialize the runtime manager
     */
    initialize(): Promise<void>;
    /**
     * Start the runtime process
     */
    start(): Promise<void>;
    /**
     * Stop the runtime process
     */
    stop(): Promise<void>;
    /**
     * Get the current runtime status
     */
    getStatus(): RuntimeStatus;
    /**
     * Get the runtime port
     */
    getPort(): number | null;
    /**
     * Shutdown the runtime manager
     */
    shutdown(): Promise<void>;
}
