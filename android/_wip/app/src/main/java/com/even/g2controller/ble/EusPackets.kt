package com.even.g2controller.ble

object EusUuids {
    const val SERVICE = "00002760-08c2-11e1-9073-0e8ac72e5450"
    const val TX_CHAR = "00002760-08c2-11e1-9073-0e8ac72e5401"
    const val RX_CHAR = "00002760-08c2-11e1-9073-0e8ac72e5402"
}

class EusFrame(
    val flags: Byte,
    val command: Byte,
    val payload: ByteArray,
    val checksum: Byte
) {
    companion object {
        const val FLAG_CMD = 0x01.toByte()
        const val FLAG_RESP = 0x02.toByte()
        const val FLAG_NOTIFY = 0x04.toByte()
        
        const val CMD_PING = 0x01.toByte()
        const val CMD_AUTH = 0x02.toByte()
        const val CMD_DISPLAY_TEXT = 0x10.toByte()
        const val CMD_DISPLAY_IMAGE = 0x11.toByte()
        const val CMD_CLEAR = 0x12.toByte()
        const val CMD_SCROLL = 0x20.toByte()
        const val CMD_TAP = 0x21.toByte()
        const val CMD_LONG_TAP = 0x22.toByte()
        const val CMD_DOUBLE_TAP = 0x23.toByte()
        const val CMD_NAV_BACK = 0x24.toByte()
        const val CMD_NAV_HOME = 0x25.toByte()
        
        fun buildCommand(command: Byte, payload: ByteArray = ByteArray(0)): ByteArray {
            val length = (payload.size + 1).toShort()
            val buffer = ByteArray(4 + payload.size + 1)
            buffer[0] = FLAG_CMD
            buffer[1] = (length.toInt().shr(8)).toByte()
            buffer[2] = (length.toInt().and(0xFF)).toByte()
            buffer[3] = command
            payload.copyInto(buffer, 4)
            val checksum = buffer.take(4 + payload.size).fold(0) { acc, b -> acc xor b.toInt() }.toByte()
            buffer[buffer.lastIndex] = checksum
            return buffer
        }
        
        fun parse(data: ByteArray): EusFrame? {
            if (data.size < 5) return null
            val flags = data[0]
            val length = ((data[1].toInt().and(0xFF)).shl(8)) or (data[2].toInt().and(0xFF))
            if (data.size != 3 + length + 1) return null
            val command = data[3]
            val payload = data.copyOfRange(4, 4 + length - 1)
            val checksum = data[data.lastIndex]
            val computed = data.take(data.size - 1).fold(0) { acc, b -> acc xor b.toInt() }.toByte()
            if (computed != checksum) return null
            return EusFrame(flags, command, payload, checksum)
        }
    }
    
    fun toByteArray(): ByteArray = buildCommand(command, payload)
}

object G2Commands {
    fun scrollUp(lines: Int = 3) = EusFrame.buildCommand(EusFrame.CMD_SCROLL, byteArrayOf(0x00, lines.toByte()))
    fun scrollDown(lines: Int = 3) = EusFrame.buildCommand(EusFrame.CMD_SCROLL, byteArrayOf(0x01, lines.toByte()))
    fun tap() = EusFrame.buildCommand(EusFrame.CMD_TAP)
    fun longTap() = EusFrame.buildCommand(EusFrame.CMD_LONG_TAP)
    fun doubleTap() = EusFrame.buildCommand(EusFrame.CMD_DOUBLE_TAP)
    fun navBack() = EusFrame.buildCommand(EusFrame.CMD_NAV_BACK)
    fun navHome() = EusFrame.buildCommand(EusFrame.CMD_NAV_HOME)
    fun ping() = EusFrame.buildCommand(EusFrame.CMD_PING)
    fun auth(challenge: ByteArray) = EusFrame.buildCommand(EusFrame.CMD_AUTH, challenge)
}
