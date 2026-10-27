// Package identity creates the node id, bearer token, and TLS certificate once.
// Those files live under the config directory and are never rewritten on upgrade.
package identity

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"time"
)

// Store is the persisted identity of one agent.
type Store struct {
	Dir         string
	NodeID      string
	Token       string
	Fingerprint string
	cert        tls.Certificate
}

// Open loads an existing identity or creates one. Existing files are left as they are.
func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return nil, err
	}
	nodeID, err := loadOrCreate(filepath.Join(dir, "node_id"), newUUID, 0o600)
	if err != nil {
		return nil, err
	}
	token, err := loadOrCreate(filepath.Join(dir, "token"), newToken, 0o600)
	if err != nil {
		return nil, err
	}
	cert, fp, err := loadOrCreateCert(dir)
	if err != nil {
		return nil, err
	}
	return &Store{Dir: dir, NodeID: nodeID, Token: token, Fingerprint: fp, cert: cert}, nil
}

// TLSConfig presents the node certificate. Clients pin Fingerprint instead of a public CA.
func (s *Store) TLSConfig() *tls.Config {
	return &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{s.cert},
	}
}

func loadOrCreate(path string, create func() (string, error), mode os.FileMode) (string, error) {
	raw, err := os.ReadFile(path)
	if err == nil {
		text := string(trim(raw))
		if text == "" {
			return "", fmt.Errorf("%s is empty", path)
		}
		return text, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	value, err := create()
	if err != nil {
		return "", err
	}
	if err := writeNew(path, []byte(value+"\n"), mode); err != nil {
		return "", err
	}
	return value, nil
}

func loadOrCreateCert(dir string) (tls.Certificate, string, error) {
	certPath := filepath.Join(dir, "tls.crt")
	keyPath := filepath.Join(dir, "tls.key")
	certPEM, certErr := os.ReadFile(certPath)
	keyPEM, keyErr := os.ReadFile(keyPath)
	if certErr == nil && keyErr == nil {
		pair, err := tls.X509KeyPair(certPEM, keyPEM)
		if err != nil {
			return tls.Certificate{}, "", err
		}
		return pair, fingerprintPEM(certPEM), nil
	}
	if (certErr != nil && !errors.Is(certErr, os.ErrNotExist)) || (keyErr != nil && !errors.Is(keyErr, os.ErrNotExist)) {
		return tls.Certificate{}, "", errors.Join(certErr, keyErr)
	}
	certPEM, keyPEM, err := generateCert()
	if err != nil {
		return tls.Certificate{}, "", err
	}
	if err := writeNew(certPath, certPEM, 0o644); err != nil {
		return tls.Certificate{}, "", err
	}
	if err := writeNew(keyPath, keyPEM, 0o600); err != nil {
		return tls.Certificate{}, "", err
	}
	pair, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		return tls.Certificate{}, "", err
	}
	return pair, fingerprintPEM(certPEM), nil
}

func generateCert() (certPEM, keyPEM []byte, err error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, nil, err
	}
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "ravn"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(10 * 365 * 24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     []string{"ravn"},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, nil, err
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, nil, err
	}
	certPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM = pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	return certPEM, keyPEM, nil
}

func fingerprintPEM(certPEM []byte) string {
	block, _ := pem.Decode(certPEM)
	if block == nil {
		return ""
	}
	sum := sha256.Sum256(block.Bytes)
	return hex.EncodeToString(sum[:])
}

func writeNew(path string, data []byte, mode os.FileMode) error {
	tmp := path + ".partial"
	if err := os.WriteFile(tmp, data, mode); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func newToken() (string, error) {
	var b [32]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}

func newUUID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}

func trim(b []byte) []byte {
	for len(b) > 0 && (b[len(b)-1] == '\n' || b[len(b)-1] == '\r' || b[len(b)-1] == ' ') {
		b = b[:len(b)-1]
	}
	return b
}
