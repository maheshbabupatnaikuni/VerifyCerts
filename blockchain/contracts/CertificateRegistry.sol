// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract CertificateRegistry {
    struct CertificateRecord {
        string certificateId;
        string hashValue;
        uint256 timestamp;
        address issuer;
        bool exists;
    }

    mapping(string => CertificateRecord) private certificates;

    event CertificateStored(string indexed certificateId, string hashValue, uint256 timestamp, address issuer);

    function storeCertificate(string calldata certificateId, string calldata hashValue) external {
        require(!certificates[certificateId].exists, "Certificate ID already exists");
        certificates[certificateId] = CertificateRecord({
            certificateId: certificateId,
            hashValue: hashValue,
            timestamp: block.timestamp,
            issuer: msg.sender,
            exists: true
        });

        emit CertificateStored(certificateId, hashValue, block.timestamp, msg.sender);
    }

    function getCertificate(string calldata certificateId)
        external
        view
        returns (string memory hashValue, uint256 timestamp, address issuer, bool exists)
    {
        CertificateRecord memory cert = certificates[certificateId];
        return (cert.hashValue, cert.timestamp, cert.issuer, cert.exists);
    }
}
