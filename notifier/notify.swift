import Foundation

let args = CommandLine.arguments
guard args.count >= 3 else {
    fputs("Usage: notify <title> <message>\n", stderr)
    exit(1)
}

let notification = NSUserNotification()
notification.title = args[1]
notification.informativeText = args[2]

let center = NSUserNotificationCenter.default
center.deliver(notification)

RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.5))
